(function initSpeakerXmlRepair() {
  function normalizeName(value) {
    return String(value || "").trim().toLowerCase();
  }

  function decodeFileUrl(pathUrl) {
    const raw = String(pathUrl || "").trim();
    if (!raw) return "";
    if (!raw.startsWith("file://")) return raw;
    try {
      const parsed = new URL(raw);
      let pathname = decodeURIComponent(parsed.pathname || "");
      if (/^\/[A-Za-z]:\//.test(pathname)) {
        pathname = pathname.slice(1);
      }
      if (parsed.host) {
        return `//${parsed.host}${pathname}`;
      }
      return pathname;
    } catch (_error) {
      return raw;
    }
  }

  function encodeFileUrl(filePath) {
    const raw = String(filePath || "").trim();
    if (!raw) return "";
    if (raw.startsWith("file://")) return raw;
    const normalized = raw.replace(/\\/g, "/");
    if (/^[A-Za-z]:\//.test(normalized)) {
      const segments = normalized.split("/").map((segment, index) => (
        index === 0 ? segment : encodeURIComponent(segment)
      ));
      return `file:///${segments.join("/")}`;
    }
    const encodedPath = normalized
      .split("/")
      .map((segment, index) => (index === 0 && !segment ? "" : encodeURIComponent(segment)))
      .join("/");
    return `file://${encodedPath}`;
  }

  function getNodeText(parent, tagName) {
    if (!parent) return "";
    const node = Array.from(parent.children || []).find((child) => child.tagName === tagName);
    return node?.textContent || "";
  }

  function setNodeText(parent, tagName, value) {
    if (!parent) return;
    let node = Array.from(parent.children || []).find((child) => child.tagName === tagName);
    if (!node) {
      node = parent.ownerDocument.createElement(tagName);
      parent.appendChild(node);
    }
    node.textContent = value;
  }

  function buildFileKey(fileNode, fallbackIndex) {
    const id = fileNode.getAttribute("id");
    const pathUrl = getNodeText(fileNode, "pathurl");
    const name = getNodeText(fileNode, "name");
    return id || pathUrl || `${name || "png"}-${fallbackIndex}`;
  }

  function isPngResource(fileNode) {
    const pathUrl = getNodeText(fileNode, "pathurl");
    const name = getNodeText(fileNode, "name");
    return /\.png(?:$|\?)/i.test(pathUrl) || /\.png$/i.test(name);
  }

  function parseXmlRepairDocument(xmlFile, xmlText) {
    const parser = new DOMParser();
    const documentNode = parser.parseFromString(xmlText, "application/xml");
    const parserError = documentNode.querySelector("parsererror");
    if (parserError) {
      throw new Error("XML 解析失败，请确认这是 Premiere 导出的 XML 文件");
    }

    const assetMap = new Map();
    const clipItems = Array.from(documentNode.getElementsByTagName("clipitem"));
    clipItems.forEach((clipItem) => {
      const fileNode = Array.from(clipItem.getElementsByTagName("file")).find(isPngResource);
      if (!fileNode) return;
      const key = buildFileKey(fileNode, assetMap.size);
      const oldPathUrl = getNodeText(fileNode, "pathurl");
      const oldName = getNodeText(fileNode, "name") || decodeFileUrl(oldPathUrl).split("/").pop() || "未命名 PNG";
      const oldPath = decodeFileUrl(oldPathUrl);
      if (!assetMap.has(key)) {
        assetMap.set(key, {
          id: key,
          fileId: fileNode.getAttribute("id") || "",
          oldName,
          oldPath,
          oldPathUrl,
          usageCount: 0,
          replacementPath: "",
          replacementName: "",
          replacementPathUrl: "",
        });
      }
      assetMap.get(key).usageCount += 1;
    });

    const assets = Array.from(assetMap.values()).sort((left, right) => left.oldName.localeCompare(right.oldName, "zh-CN"));
    return {
      xmlFile,
      xmlText,
      assets,
    };
  }

  function autoMatchAssets(assets, replacementFiles) {
    const exactMap = new Map();
    const stemMap = new Map();
    (replacementFiles || []).forEach((file) => {
      exactMap.set(normalizeName(file.name), file);
      const stem = normalizeName(file.name.replace(/\.[^.]+$/, ""));
      if (!stemMap.has(stem)) stemMap.set(stem, file);
    });

    return (assets || []).map((asset) => {
      const exact = exactMap.get(normalizeName(asset.oldName));
      const stem = stemMap.get(normalizeName(asset.oldName.replace(/\.[^.]+$/, "")));
      const matched = exact || stem || null;
      return {
        ...asset,
        replacementPath: matched?.path || asset.replacementPath || "",
        replacementName: matched?.name || asset.replacementName || "",
        replacementPathUrl: matched?.path ? encodeFileUrl(matched.path) : asset.replacementPathUrl || "",
      };
    });
  }

  function setAssetReplacement(assets, assetId, replacementFile) {
    return (assets || []).map((asset) => {
      if (asset.id !== assetId) return asset;
      if (!replacementFile) {
        return {
          ...asset,
          replacementPath: "",
          replacementName: "",
          replacementPathUrl: "",
        };
      }
      return {
        ...asset,
        replacementPath: replacementFile.path,
        replacementName: replacementFile.name,
        replacementPathUrl: encodeFileUrl(replacementFile.path),
      };
    });
  }

  function clearAssetReplacements(assets) {
    return (assets || []).map((asset) => ({
      ...asset,
      replacementPath: "",
      replacementName: "",
      replacementPathUrl: "",
    }));
  }

  function applyXmlReplacements(xmlText, assets) {
    const parser = new DOMParser();
    const documentNode = parser.parseFromString(xmlText, "application/xml");
    const parserError = documentNode.querySelector("parsererror");
    if (parserError) {
      throw new Error("XML 解析失败，无法导出 patched XML");
    }

    const replacements = (assets || []).filter((asset) => asset.replacementPathUrl);
    if (!replacements.length) {
      throw new Error("请至少指定一张新版 PNG 再导出 XML");
    }

    const fileNodes = Array.from(documentNode.getElementsByTagName("file"));
    let replacedCount = 0;
    replacements.forEach((asset) => {
      fileNodes.forEach((fileNode, index) => {
        const key = buildFileKey(fileNode, index);
        const currentPathUrl = getNodeText(fileNode, "pathurl");
        const currentName = getNodeText(fileNode, "name");
        const matches = key === asset.id
          || (asset.fileId && fileNode.getAttribute("id") === asset.fileId)
          || (asset.oldPathUrl && currentPathUrl === asset.oldPathUrl)
          || (asset.oldName && currentName === asset.oldName);
        if (!matches) return;
        setNodeText(fileNode, "pathurl", asset.replacementPathUrl);
        if (asset.replacementName) {
          setNodeText(fileNode, "name", asset.replacementName);
        }
        replacedCount += 1;
      });
    });

    const serializer = new XMLSerializer();
    const xmlBody = serializer.serializeToString(documentNode.documentElement);
    const doctype = documentNode.doctype?.name ? `<!DOCTYPE ${documentNode.doctype.name}>` : "<!DOCTYPE xmeml>";
    return {
      replacedCount,
      xmlText: `<?xml version="1.0" encoding="UTF-8"?>\n${doctype}\n${xmlBody}\n`,
    };
  }

  function buildPatchedXmlPath(xmlPath) {
    const raw = String(xmlPath || "").trim();
    if (!raw) return "patched.xml";
    return raw.replace(/\.xml$/i, ".patched.xml");
  }

  window.SpeakerXmlRepair = {
    parseXmlRepairDocument,
    autoMatchAssets,
    setAssetReplacement,
    clearAssetReplacements,
    applyXmlReplacements,
    buildPatchedXmlPath,
  };
})();
