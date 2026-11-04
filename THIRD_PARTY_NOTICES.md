# Third-party notices

Speaker 自有源码采用 MIT。依赖、打包工具、系统框架和模型保留各自许可证，本文件不重新许可它们。完整收集的许可文本见 [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt)。

客户端使用 Tauri/Rust、Tabler SVG 图标，以及 PyInstaller 封装的 Python 字幕核心。Python 使用 PSF 许可证；PyInstaller 使用 GPL 加 bootloader 发布例外，参见收集的 COPYING 文本。Apple SpeechAnalyzer 由 macOS 提供，模型资源不在本仓库。阿里云、DeepSeek 等远端服务遵守各自服务及模型条款。FFmpeg 是由用户另行安装的外部程序，本测试包不捆绑 FFmpeg。

下表来自构建清单，包含开发/可选/传递依赖，不表示全部组件均随安装包加载。MPL 等文件级许可证组件继续按上游许可证提供；未修改的上游源码可由对应版本的官方仓库或包注册表获取。

| Group | Component | Version | License | Upstream |
| --- | --- | --- | --- | --- |
| Rust | adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 | [source](https://github.com/oyvindln/adler2) |
| Rust | aho-corasick | 1.1.4 | Unlicense OR MIT | [source](https://github.com/BurntSushi/aho-corasick) |
| Rust | alloc-no-stdlib | 2.0.4 | BSD-3-Clause | [source](https://github.com/dropbox/rust-alloc-no-stdlib) |
| Rust | alloc-stdlib | 0.2.2 | BSD-3-Clause | [source](https://github.com/dropbox/rust-alloc-no-stdlib) |
| Rust | anyhow | 1.0.102 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/anyhow) |
| Rust | atomic-waker | 1.1.2 | Apache-2.0 OR MIT | [source](https://github.com/smol-rs/atomic-waker) |
| Rust | autocfg | 1.5.1 | Apache-2.0 OR MIT | [source](https://github.com/cuviper/autocfg) |
| Rust | base64 | 0.21.7 | MIT OR Apache-2.0 | [source](https://github.com/marshallpierce/rust-base64) |
| Rust | base64 | 0.22.1 | MIT OR Apache-2.0 | [source](https://github.com/marshallpierce/rust-base64) |
| Rust | bit-set | 0.8.0 | Apache-2.0 OR MIT | [source](https://github.com/contain-rs/bit-set) |
| Rust | bit-vec | 0.8.0 | Apache-2.0 OR MIT | [source](https://github.com/contain-rs/bit-vec) |
| Rust | bitflags | 1.3.2 | MIT/Apache-2.0 | [source](https://github.com/bitflags/bitflags) |
| Rust | bitflags | 2.11.1 | MIT OR Apache-2.0 | [source](https://github.com/bitflags/bitflags) |
| Rust | block-buffer | 0.10.4 | MIT OR Apache-2.0 | [source](https://github.com/RustCrypto/utils) |
| Rust | block2 | 0.6.2 | MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | brotli | 8.0.3 | BSD-3-Clause AND MIT | [source](https://github.com/dropbox/rust-brotli) |
| Rust | brotli-decompressor | 5.0.1 | BSD-3-Clause/MIT | [source](https://github.com/dropbox/rust-brotli-decompressor) |
| Rust | bs58 | 0.5.1 | MIT/Apache-2.0 | [source](https://github.com/Nullus157/bs58-rs) |
| Rust | byteorder | 1.5.0 | Unlicense OR MIT | [source](https://github.com/BurntSushi/byteorder) |
| Rust | bytes | 1.11.1 | MIT | [source](https://github.com/tokio-rs/bytes) |
| Rust | camino | 1.2.2 | MIT OR Apache-2.0 | [source](https://github.com/camino-rs/camino) |
| Rust | cargo-platform | 0.1.9 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/cargo) |
| Rust | cargo_metadata | 0.19.2 | MIT | [source](https://github.com/oli-obk/cargo_metadata) |
| Rust | cargo_toml | 0.22.3 | Apache-2.0 OR MIT | [source](https://gitlab.com/lib.rs/cargo_toml) |
| Rust | cc | 1.2.62 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/cc-rs) |
| Rust | cfb | 0.7.3 | MIT | [source](https://github.com/mdsteele/rust-cfb) |
| Rust | cfg-if | 1.0.4 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/cfg-if) |
| Rust | chrono | 0.4.44 | MIT OR Apache-2.0 | [source](https://github.com/chronotope/chrono) |
| Rust | cookie | 0.18.1 | MIT OR Apache-2.0 | [source](https://github.com/SergioBenitez/cookie-rs) |
| Rust | core-foundation | 0.10.1 | MIT OR Apache-2.0 | [source](https://github.com/servo/core-foundation-rs) |
| Rust | core-foundation-sys | 0.8.7 | MIT OR Apache-2.0 | [source](https://github.com/servo/core-foundation-rs) |
| Rust | core-graphics | 0.25.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/core-foundation-rs) |
| Rust | core-graphics-types | 0.2.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/core-foundation-rs) |
| Rust | cpufeatures | 0.2.17 | MIT OR Apache-2.0 | [source](https://github.com/RustCrypto/utils) |
| Rust | crc32fast | 1.5.0 | MIT OR Apache-2.0 | [source](https://github.com/srijs/rust-crc32fast) |
| Rust | crossbeam-channel | 0.5.15 | MIT OR Apache-2.0 | [source](https://github.com/crossbeam-rs/crossbeam) |
| Rust | crossbeam-utils | 0.8.21 | MIT OR Apache-2.0 | [source](https://github.com/crossbeam-rs/crossbeam) |
| Rust | crypto-common | 0.1.7 | MIT OR Apache-2.0 | [source](https://github.com/RustCrypto/traits) |
| Rust | cssparser | 0.36.0 | MPL-2.0 | [source](https://github.com/servo/rust-cssparser) |
| Rust | cssparser-macros | 0.6.1 | MPL-2.0 | [source](https://github.com/servo/rust-cssparser) |
| Rust | ctor | 0.8.0 | Apache-2.0 OR MIT | [source](https://github.com/mmastrac/rust-ctor) |
| Rust | ctor-proc-macro | 0.0.7 | Apache-2.0 OR MIT | [source](https://github.com/mmastrac/rust-ctor) |
| Rust | darling | 0.23.0 | MIT | [source](https://github.com/TedDriggs/darling) |
| Rust | darling_core | 0.23.0 | MIT | [source](https://github.com/TedDriggs/darling) |
| Rust | darling_macro | 0.23.0 | MIT | [source](https://github.com/TedDriggs/darling) |
| Rust | deranged | 0.5.8 | MIT OR Apache-2.0 | [source](https://github.com/jhpratt/deranged) |
| Rust | derive_more | 2.1.1 | MIT | [source](https://github.com/JelteF/derive_more) |
| Rust | derive_more-impl | 2.1.1 | MIT | [source](https://github.com/JelteF/derive_more) |
| Rust | digest | 0.10.7 | MIT OR Apache-2.0 | [source](https://github.com/RustCrypto/traits) |
| Rust | dirs | 6.0.0 | MIT OR Apache-2.0 | [source](https://github.com/soc/dirs-rs) |
| Rust | dirs-sys | 0.5.0 | MIT OR Apache-2.0 | [source](https://github.com/dirs-dev/dirs-sys-rs) |
| Rust | dispatch2 | 0.3.1 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | displaydoc | 0.2.6 | MIT OR Apache-2.0 | [source](https://github.com/yaahc/displaydoc) |
| Rust | dom_query | 0.27.0 | MIT | [source](https://github.com/niklak/dom_query) |
| Rust | dpi | 0.1.2 | Apache-2.0 AND MIT | [source](https://github.com/rust-windowing/winit) |
| Rust | dtoa | 1.0.11 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/dtoa) |
| Rust | dtoa-short | 0.3.5 | MPL-2.0 | [source](https://github.com/upsuper/dtoa-short) |
| Rust | dtor | 0.3.0 | Apache-2.0 OR MIT | [source](https://github.com/mmastrac/rust-ctor) |
| Rust | dtor-proc-macro | 0.0.6 | Apache-2.0 OR MIT | [source](https://github.com/mmastrac/rust-ctor) |
| Rust | dunce | 1.0.5 | CC0-1.0 OR MIT-0 OR Apache-2.0 | [source](https://gitlab.com/kornelski/dunce) |
| Rust | dyn-clone | 1.0.20 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/dyn-clone) |
| Rust | embed-resource | 3.0.9 | MIT | [source](https://github.com/nabijaczleweli/rust-embed-resource) |
| Rust | embed_plist | 1.2.2 | MIT OR Apache-2.0 | [source](https://github.com/nvzqz/embed-plist-rs) |
| Rust | equivalent | 1.0.2 | Apache-2.0 OR MIT | [source](https://github.com/indexmap-rs/equivalent) |
| Rust | erased-serde | 0.4.10 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/erased-serde) |
| Rust | errno | 0.3.14 | MIT OR Apache-2.0 | [source](https://github.com/lambda-fairy/rust-errno) |
| Rust | fastrand | 2.4.1 | Apache-2.0 OR MIT | [source](https://github.com/smol-rs/fastrand) |
| Rust | fdeflate | 0.3.7 | MIT OR Apache-2.0 | [source](https://github.com/image-rs/fdeflate) |
| Rust | filetime | 0.2.29 | MIT/Apache-2.0 | [source](https://github.com/alexcrichton/filetime) |
| Rust | find-msvc-tools | 0.1.9 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/cc-rs) |
| Rust | flate2 | 1.1.9 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/flate2-rs) |
| Rust | fnv | 1.0.7 | Apache-2.0 / MIT | [source](https://github.com/servo/rust-fnv) |
| Rust | foldhash | 0.2.0 | Zlib | [source](https://github.com/orlp/foldhash) |
| Rust | foreign-types | 0.5.0 | MIT/Apache-2.0 | [source](https://github.com/sfackler/foreign-types) |
| Rust | foreign-types-macros | 0.2.3 | MIT/Apache-2.0 | [source](https://github.com/sfackler/foreign-types) |
| Rust | foreign-types-shared | 0.3.1 | MIT/Apache-2.0 | [source](https://github.com/sfackler/foreign-types) |
| Rust | form_urlencoded | 1.2.2 | MIT OR Apache-2.0 | [source](https://github.com/servo/rust-url) |
| Rust | futures-channel | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-core | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-io | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-macro | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-sink | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-task | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | futures-util | 0.3.32 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/futures-rs) |
| Rust | generic-array | 0.14.7 | MIT | [source](https://github.com/fizyk20/generic-array.git) |
| Rust | getrandom | 0.2.17 | MIT OR Apache-2.0 | [source](https://github.com/rust-random/getrandom) |
| Rust | getrandom | 0.3.4 | MIT OR Apache-2.0 | [source](https://github.com/rust-random/getrandom) |
| Rust | getrandom | 0.4.2 | MIT OR Apache-2.0 | [source](https://github.com/rust-random/getrandom) |
| Rust | glob | 0.3.3 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/glob) |
| Rust | hashbrown | 0.12.3 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/hashbrown) |
| Rust | hashbrown | 0.17.1 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/hashbrown) |
| Rust | heck | 0.5.0 | MIT OR Apache-2.0 | [source](https://github.com/withoutboats/heck) |
| Rust | hex | 0.4.3 | MIT OR Apache-2.0 | [source](https://github.com/KokaKiwi/rust-hex) |
| Rust | html5ever | 0.38.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/html5ever) |
| Rust | http | 1.4.1 | MIT OR Apache-2.0 | [source](https://github.com/hyperium/http) |
| Rust | http-body | 1.0.1 | MIT | [source](https://github.com/hyperium/http-body) |
| Rust | http-body-util | 0.1.3 | MIT | [source](https://github.com/hyperium/http-body) |
| Rust | http-range | 0.1.5 | MIT | [source](https://github.com/bancek/rust-http-range.git) |
| Rust | httparse | 1.10.1 | MIT OR Apache-2.0 | [source](https://github.com/seanmonstar/httparse) |
| Rust | hyper | 1.10.0 | MIT | [source](https://github.com/hyperium/hyper) |
| Rust | hyper-rustls | 0.27.9 | Apache-2.0 OR ISC OR MIT | [source](https://github.com/rustls/hyper-rustls) |
| Rust | hyper-util | 0.1.20 | MIT | [source](https://github.com/hyperium/hyper-util) |
| Rust | iana-time-zone | 0.1.65 | MIT OR Apache-2.0 | [source](https://github.com/strawlab/iana-time-zone) |
| Rust | ico | 0.5.0 | MIT | [source](https://github.com/mdsteele/rust-ico) |
| Rust | icu_collections | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_locale_core | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_normalizer | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_normalizer_data | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_properties | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_properties_data | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | icu_provider | 2.2.0 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | ident_case | 1.0.1 | MIT/Apache-2.0 | [source](https://github.com/TedDriggs/ident_case) |
| Rust | idna | 1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/rust-url/) |
| Rust | idna_adapter | 1.2.2 | Apache-2.0 OR MIT | [source](https://github.com/hsivonen/idna_adapter) |
| Rust | indexmap | 1.9.3 | Apache-2.0 OR MIT | [source](https://github.com/bluss/indexmap) |
| Rust | indexmap | 2.14.0 | Apache-2.0 OR MIT | [source](https://github.com/indexmap-rs/indexmap) |
| Rust | infer | 0.19.0 | MIT | [source](https://github.com/bojand/infer) |
| Rust | ipnet | 2.12.0 | MIT OR Apache-2.0 | [source](https://github.com/krisprice/ipnet) |
| Rust | itoa | 1.0.18 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/itoa) |
| Rust | json-patch | 3.0.1 | MIT/Apache-2.0 | [source](https://github.com/idubrov/json-patch) |
| Rust | jsonptr | 0.6.3 | MIT OR Apache-2.0 | [source](https://github.com/chanced/jsonptr) |
| Rust | keyboard-types | 0.7.0 | MIT OR Apache-2.0 | [source](https://github.com/pyfisch/keyboard-types) |
| Rust | libc | 0.2.186 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/libc) |
| Rust | litemap | 0.8.2 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | lock_api | 0.4.14 | MIT OR Apache-2.0 | [source](https://github.com/Amanieu/parking_lot) |
| Rust | log | 0.4.30 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/log) |
| Rust | markup5ever | 0.38.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/html5ever) |
| Rust | memchr | 2.8.1 | Unlicense OR MIT | [source](https://github.com/BurntSushi/memchr) |
| Rust | mime | 0.3.17 | MIT OR Apache-2.0 | [source](https://github.com/hyperium/mime) |
| Rust | minisign-verify | 0.2.5 | MIT | [source](https://github.com/jedisct1/rust-minisign-verify) |
| Rust | miniz_oxide | 0.8.9 | MIT OR Zlib OR Apache-2.0 | [source](https://github.com/Frommi/miniz_oxide/tree/master/miniz_oxide) |
| Rust | mio | 1.2.0 | MIT | [source](https://github.com/tokio-rs/mio) |
| Rust | muda | 0.19.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/muda) |
| Rust | new_debug_unreachable | 1.0.6 | MIT | [source](https://github.com/mbrubeck/rust-debug-unreachable) |
| Rust | num-conv | 0.2.2 | MIT OR Apache-2.0 | [source](https://github.com/jhpratt/num-conv) |
| Rust | num-traits | 0.2.19 | MIT OR Apache-2.0 | [source](https://github.com/rust-num/num-traits) |
| Rust | objc2 | 0.6.4 | MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-app-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-core-foundation | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-core-graphics | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-encode | 4.1.0 | MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-exception-helper | 0.1.1 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-foundation | 0.3.2 | MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-io-surface | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-osa-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | objc2-web-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/madsmtm/objc2) |
| Rust | once_cell | 1.21.4 | MIT OR Apache-2.0 | [source](https://github.com/matklad/once_cell) |
| Rust | open | 5.3.5 | MIT | [source](https://github.com/Byron/open-rs) |
| Rust | option-ext | 0.2.0 | MPL-2.0 | [source](https://github.com/soc/option-ext.git) |
| Rust | osakit | 0.3.1 | MIT OR Apache-2.0 | [source](https://github.com/mdevils/rust-osakit) |
| Rust | parking_lot | 0.12.5 | MIT OR Apache-2.0 | [source](https://github.com/Amanieu/parking_lot) |
| Rust | parking_lot_core | 0.9.12 | MIT OR Apache-2.0 | [source](https://github.com/Amanieu/parking_lot) |
| Rust | pathdiff | 0.2.3 | MIT/Apache-2.0 | [source](https://github.com/Manishearth/pathdiff) |
| Rust | percent-encoding | 2.3.2 | MIT OR Apache-2.0 | [source](https://github.com/servo/rust-url/) |
| Rust | phf | 0.13.1 | MIT | [source](https://github.com/rust-phf/rust-phf) |
| Rust | phf_codegen | 0.13.1 | MIT | [source](https://github.com/rust-phf/rust-phf) |
| Rust | phf_generator | 0.13.1 | MIT | [source](https://github.com/rust-phf/rust-phf) |
| Rust | phf_macros | 0.13.1 | MIT | [source](https://github.com/rust-phf/rust-phf) |
| Rust | phf_shared | 0.13.1 | MIT | [source](https://github.com/rust-phf/rust-phf) |
| Rust | pin-project-lite | 0.2.17 | Apache-2.0 OR MIT | [source](https://github.com/taiki-e/pin-project-lite) |
| Rust | plist | 1.9.0 | MIT | [source](https://github.com/ebarnard/rust-plist/) |
| Rust | png | 0.17.16 | MIT OR Apache-2.0 | [source](https://github.com/image-rs/image-png) |
| Rust | png | 0.18.1 | MIT OR Apache-2.0 | [source](https://github.com/image-rs/image-png) |
| Rust | potential_utf | 0.1.5 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | powerfmt | 0.2.0 | MIT OR Apache-2.0 | [source](https://github.com/jhpratt/powerfmt) |
| Rust | precomputed-hash | 0.1.1 | MIT | [source](https://github.com/emilio/precomputed-hash) |
| Rust | proc-macro2 | 1.0.106 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/proc-macro2) |
| Rust | quick-xml | 0.39.4 | MIT | [source](https://github.com/tafia/quick-xml) |
| Rust | quote | 1.0.45 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/quote) |
| Rust | raw-window-handle | 0.6.2 | MIT OR Apache-2.0 OR Zlib | [source](https://github.com/rust-windowing/raw-window-handle) |
| Rust | ref-cast | 1.0.25 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/ref-cast) |
| Rust | ref-cast-impl | 1.0.25 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/ref-cast) |
| Rust | regex | 1.12.3 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/regex) |
| Rust | regex-automata | 0.4.14 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/regex) |
| Rust | regex-syntax | 0.8.10 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/regex) |
| Rust | reqwest | 0.13.4 | MIT OR Apache-2.0 | [source](https://github.com/seanmonstar/reqwest) |
| Rust | rfd | 0.16.0 | MIT | [source](https://github.com/PolyMeilex/rfd) |
| Rust | ring | 0.17.14 | Apache-2.0 AND ISC | [source](https://github.com/briansmith/ring) |
| Rust | rustc-hash | 2.1.2 | Apache-2.0 OR MIT | [source](https://github.com/rust-lang/rustc-hash) |
| Rust | rustc_version | 0.4.1 | MIT OR Apache-2.0 | [source](https://github.com/djc/rustc-version-rs) |
| Rust | rustix | 1.1.4 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | [source](https://github.com/bytecodealliance/rustix) |
| Rust | rustls | 0.23.40 | Apache-2.0 OR ISC OR MIT | [source](https://github.com/rustls/rustls) |
| Rust | rustls-pki-types | 1.14.1 | MIT OR Apache-2.0 | [source](https://github.com/rustls/pki-types) |
| Rust | rustls-platform-verifier | 0.7.0 | MIT OR Apache-2.0 | [source](https://github.com/rustls/rustls-platform-verifier) |
| Rust | rustls-webpki | 0.103.13 | ISC | [source](https://github.com/rustls/webpki) |
| Rust | same-file | 1.0.6 | Unlicense/MIT | [source](https://github.com/BurntSushi/same-file) |
| Rust | schemars | 0.8.22 | MIT | [source](https://github.com/GREsau/schemars) |
| Rust | schemars | 0.9.0 | MIT | [source](https://github.com/GREsau/schemars) |
| Rust | schemars | 1.2.1 | MIT | [source](https://github.com/GREsau/schemars) |
| Rust | schemars_derive | 0.8.22 | MIT | [source](https://github.com/GREsau/schemars) |
| Rust | scopeguard | 1.2.0 | MIT OR Apache-2.0 | [source](https://github.com/bluss/scopeguard) |
| Rust | security-framework | 3.7.0 | MIT OR Apache-2.0 | [source](https://github.com/kornelski/rust-security-framework) |
| Rust | security-framework-sys | 2.17.0 | MIT OR Apache-2.0 | [source](https://github.com/kornelski/rust-security-framework) |
| Rust | selectors | 0.36.1 | MPL-2.0 | [source](https://github.com/servo/stylo) |
| Rust | semver | 1.0.28 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/semver) |
| Rust | serde | 1.0.228 | MIT OR Apache-2.0 | [source](https://github.com/serde-rs/serde) |
| Rust | serde-untagged | 0.1.9 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/serde-untagged) |
| Rust | serde_core | 1.0.228 | MIT OR Apache-2.0 | [source](https://github.com/serde-rs/serde) |
| Rust | serde_derive | 1.0.228 | MIT OR Apache-2.0 | [source](https://github.com/serde-rs/serde) |
| Rust | serde_derive_internals | 0.29.1 | MIT OR Apache-2.0 | [source](https://github.com/serde-rs/serde) |
| Rust | serde_json | 1.0.150 | MIT OR Apache-2.0 | [source](https://github.com/serde-rs/json) |
| Rust | serde_repr | 0.1.20 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/serde-repr) |
| Rust | serde_spanned | 1.1.1 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | serde_with | 3.20.0 | MIT OR Apache-2.0 | [source](https://github.com/jonasbb/serde_with/) |
| Rust | serde_with_macros | 3.20.0 | MIT OR Apache-2.0 | [source](https://github.com/jonasbb/serde_with/) |
| Rust | serialize-to-javascript | 0.1.2 | MIT OR Apache-2.0 | [source](https://github.com/chippers/serialize-to-javascript) |
| Rust | serialize-to-javascript-impl | 0.1.2 | MIT OR Apache-2.0 | [source](https://github.com/chippers/serialize-to-javascript) |
| Rust | servo_arc | 0.4.3 | MIT OR Apache-2.0 | [source](https://github.com/servo/stylo) |
| Rust | sha2 | 0.10.9 | MIT OR Apache-2.0 | [source](https://github.com/RustCrypto/hashes) |
| Rust | shlex | 1.3.0 | MIT OR Apache-2.0 | [source](https://github.com/comex/rust-shlex) |
| Rust | simd-adler32 | 0.3.9 | MIT | [source](https://github.com/mcountryman/simd-adler32) |
| Rust | siphasher | 1.0.3 | MIT/Apache-2.0 | [source](https://github.com/jedisct1/rust-siphash) |
| Rust | slab | 0.4.12 | MIT | [source](https://github.com/tokio-rs/slab) |
| Rust | smallvec | 1.15.1 | MIT OR Apache-2.0 | [source](https://github.com/servo/rust-smallvec) |
| Rust | socket2 | 0.6.3 | MIT OR Apache-2.0 | [source](https://github.com/rust-lang/socket2) |
| Rust | stable_deref_trait | 1.2.1 | MIT OR Apache-2.0 | [source](https://github.com/storyyeller/stable_deref_trait) |
| Rust | string_cache | 0.9.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/string-cache) |
| Rust | string_cache_codegen | 0.6.1 | MIT OR Apache-2.0 | [source](https://github.com/servo/string-cache) |
| Rust | strsim | 0.11.1 | MIT | [source](https://github.com/rapidfuzz/strsim-rs) |
| Rust | subtle | 2.6.1 | BSD-3-Clause | [source](https://github.com/dalek-cryptography/subtle) |
| Rust | swift-rs | 1.0.7 | MIT OR Apache-2.0 | [source](https://github.com/Brendonovich/swift-rs) |
| Rust | syn | 2.0.117 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/syn) |
| Rust | sync_wrapper | 1.0.2 | Apache-2.0 | [source](https://github.com/Actyx/sync_wrapper) |
| Rust | synstructure | 0.13.2 | MIT | [source](https://github.com/mystor/synstructure) |
| Rust | tao | 0.35.3 | Apache-2.0 | [source](https://github.com/tauri-apps/tao) |
| Rust | tar | 0.4.46 | MIT OR Apache-2.0 | [source](https://github.com/composefs/tar-rs) |
| Rust | tauri | 2.11.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-build | 2.6.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-codegen | 2.6.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-macros | 2.6.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-plugin | 2.6.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-plugin-dialog | 2.7.1 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/plugins-workspace) |
| Rust | tauri-plugin-fs | 2.5.1 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/plugins-workspace) |
| Rust | tauri-plugin-opener | 2.5.4 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/plugins-workspace) |
| Rust | tauri-plugin-updater | 2.10.1 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/plugins-workspace) |
| Rust | tauri-runtime | 2.11.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-runtime-wry | 2.11.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-utils | 2.9.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri) |
| Rust | tauri-winres | 0.3.6 | MIT | [source](https://github.com/tauri-apps/winres) |
| Rust | tempfile | 3.27.0 | MIT OR Apache-2.0 | [source](https://github.com/Stebalien/tempfile) |
| Rust | tendril | 0.5.0 | MIT OR Apache-2.0 | [source](https://github.com/servo/html5ever) |
| Rust | thiserror | 1.0.69 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/thiserror) |
| Rust | thiserror | 2.0.18 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/thiserror) |
| Rust | thiserror-impl | 1.0.69 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/thiserror) |
| Rust | thiserror-impl | 2.0.18 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/thiserror) |
| Rust | time | 0.3.47 | MIT OR Apache-2.0 | [source](https://github.com/time-rs/time) |
| Rust | time-core | 0.1.8 | MIT OR Apache-2.0 | [source](https://github.com/time-rs/time) |
| Rust | time-macros | 0.2.27 | MIT OR Apache-2.0 | [source](https://github.com/time-rs/time) |
| Rust | tinystr | 0.8.3 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | tinyvec | 1.11.0 | Zlib OR Apache-2.0 OR MIT | [source](https://github.com/Lokathor/tinyvec) |
| Rust | tinyvec_macros | 0.1.1 | MIT OR Apache-2.0 OR Zlib | [source](https://github.com/Soveu/tinyvec_macros) |
| Rust | tokio | 1.52.3 | MIT | [source](https://github.com/tokio-rs/tokio) |
| Rust | tokio-rustls | 0.26.4 | MIT OR Apache-2.0 | [source](https://github.com/rustls/tokio-rustls) |
| Rust | tokio-util | 0.7.18 | MIT | [source](https://github.com/tokio-rs/tokio) |
| Rust | toml | 0.9.12+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | toml | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | toml_datetime | 0.7.5+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | toml_datetime | 1.1.1+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | toml_parser | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | toml_writer | 1.1.1+spec-1.1.0 | MIT OR Apache-2.0 | [source](https://github.com/toml-rs/toml) |
| Rust | tower | 0.5.3 | MIT | [source](https://github.com/tower-rs/tower) |
| Rust | tower-http | 0.6.11 | MIT | [source](https://github.com/tower-rs/tower-http) |
| Rust | tower-layer | 0.3.3 | MIT | [source](https://github.com/tower-rs/tower) |
| Rust | tower-service | 0.3.3 | MIT | [source](https://github.com/tower-rs/tower) |
| Rust | tracing | 0.1.44 | MIT | [source](https://github.com/tokio-rs/tracing) |
| Rust | tracing-attributes | 0.1.31 | MIT | [source](https://github.com/tokio-rs/tracing) |
| Rust | tracing-core | 0.1.36 | MIT | [source](https://github.com/tokio-rs/tracing) |
| Rust | tray-icon | 0.23.1 | MIT OR Apache-2.0 | [source](https://github.com/tauri-apps/tray-icon) |
| Rust | try-lock | 0.2.5 | MIT | [source](https://github.com/seanmonstar/try-lock) |
| Rust | typeid | 1.0.3 | MIT OR Apache-2.0 | [source](https://github.com/dtolnay/typeid) |
| Rust | typenum | 1.20.0 | MIT OR Apache-2.0 | [source](https://github.com/paholg/typenum) |
| Rust | unic-char-property | 0.9.0 | MIT/Apache-2.0 | [source](https://github.com/open-i18n/rust-unic/) |
| Rust | unic-char-range | 0.9.0 | MIT/Apache-2.0 | [source](https://github.com/open-i18n/rust-unic/) |
| Rust | unic-common | 0.9.0 | MIT/Apache-2.0 | [source](https://github.com/open-i18n/rust-unic/) |
| Rust | unic-ucd-ident | 0.9.0 | MIT/Apache-2.0 | [source](https://github.com/open-i18n/rust-unic/) |
| Rust | unic-ucd-version | 0.9.0 | MIT/Apache-2.0 | [source](https://github.com/open-i18n/rust-unic/) |
| Rust | unicode-ident | 1.0.24 | (MIT OR Apache-2.0) AND Unicode-3.0 | [source](https://github.com/dtolnay/unicode-ident) |
| Rust | unicode-segmentation | 1.13.2 | MIT OR Apache-2.0 | [source](https://github.com/unicode-rs/unicode-segmentation) |
| Rust | untrusted | 0.9.0 | ISC | [source](https://github.com/briansmith/untrusted) |
| Rust | url | 2.5.8 | MIT OR Apache-2.0 | [source](https://github.com/servo/rust-url) |
| Rust | urlpattern | 0.3.0 | MIT | [source](https://github.com/denoland/rust-urlpattern) |
| Rust | utf-8 | 0.7.6 | MIT OR Apache-2.0 | [source](https://github.com/SimonSapin/rust-utf8) |
| Rust | utf8_iter | 1.0.4 | Apache-2.0 OR MIT | [source](https://github.com/hsivonen/utf8_iter) |
| Rust | uuid | 1.23.1 | Apache-2.0 OR MIT | [source](https://github.com/uuid-rs/uuid) |
| Rust | version_check | 0.9.5 | MIT/Apache-2.0 | [source](https://github.com/SergioBenitez/version_check) |
| Rust | walkdir | 2.5.0 | Unlicense/MIT | [source](https://github.com/BurntSushi/walkdir) |
| Rust | want | 0.3.1 | MIT | [source](https://github.com/seanmonstar/want) |
| Rust | web_atoms | 0.2.4 | MIT OR Apache-2.0 | [source](https://github.com/servo/html5ever) |
| Rust | window-vibrancy | 0.6.0 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri-plugin-vibrancy) |
| Rust | winnow | 0.7.15 | MIT | [source](https://github.com/winnow-rs/winnow) |
| Rust | winnow | 1.0.3 | MIT | [source](https://github.com/winnow-rs/winnow) |
| Rust | writeable | 0.6.3 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | wry | 0.55.1 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/wry) |
| Rust | xattr | 1.6.1 | MIT OR Apache-2.0 | [source](https://github.com/Stebalien/xattr) |
| Rust | yoke | 0.8.2 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | yoke-derive | 0.8.2 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zerofrom | 0.1.8 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zerofrom-derive | 0.1.7 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zeroize | 1.9.0 | Apache-2.0 OR MIT | [source](https://github.com/RustCrypto/utils) |
| Rust | zerotrie | 0.2.4 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zerovec | 0.11.6 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zerovec-derive | 0.11.3 | Unicode-3.0 | [source](https://github.com/unicode-org/icu4x) |
| Rust | zmij | 1.0.21 | MIT | [source](https://github.com/dtolnay/zmij) |
| Python | python-multipart | 0.0.20 | Apache-2.0 | [source](https://github.com/Kludex/python-multipart) |
| Python | pytest | 8.3.5 | MIT | [source](https://docs.pytest.org/en/stable/changelog.html) |
| Python | oss2 | 2.19.1 | See upstream license | [source](http://oss.aliyun.com) |
| Python | httpx | 0.28.1 | BSD-3-Clause | [source](https://github.com/encode/httpx/blob/master/CHANGELOG.md) |
| Python | opencc-python-reimplemented | 0.1.7 | Apache License | [source](https://github.com/yichen0831/opencc-python) |
| Python | iniconfig | 2.3.0 | MIT | [source](https://github.com/pytest-dev/iniconfig) |
| Python | certifi | 2026.6.17 | MPL-2.0 | [source](https://github.com/certifi/python-certifi) |
| Python | uvicorn | 0.34.2 | BSD-3-Clause | [source](https://www.uvicorn.org/release-notes) |
| Python | six | 1.17.0 | MIT | [source](https://github.com/benjaminp/six) |
| Python | anyio | 4.14.1 | MIT | [source](https://anyio.readthedocs.io/en/latest/) |
| Python | h11 | 0.16.0 | MIT | [source](https://github.com/python-hyper/h11) |
| Python | pypinyin | 0.55.0 | MIT | [source](https://github.com/mozillazg/python-pinyin) |
| Python | SQLAlchemy | 2.0.40 | MIT | [source](https://www.sqlalchemy.org) |
| Python | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [source](https://packaging.pypa.io/) |
| Python | httpcore | 1.0.9 | BSD-3-Clause | [source](https://www.encode.io/httpcore) |
| Python | fastapi | 0.115.12 | See upstream license | [source](https://github.com/fastapi/fastapi) |
| Python | pypdf | 6.14.2 | BSD-3-Clause | [source](https://github.com/py-pdf/pypdf/issues) |
| Python | pyinstaller | 6.22.3 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) | [source](https://pyinstaller.org) |
| Python | pycryptodome | 3.23.0 | BSD, Public Domain | [source](https://www.pycryptodome.org) |
| Python | macholib | 1.16.4 | MIT | [source](http://github.com/ronaldoussoren/macholib) |
| Python | aliyun-python-sdk-core | 2.16.0 | Apache License 2.0 | [source](https://github.com/aliyun/aliyun-openapi-python-sdk) |
| Python | pyinstaller-hooks-contrib | 2026.9 | See upstream license | [source](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |
| Python | pluggy | 1.6.0 | MIT |  |
| Python | jmespath | 0.10.0 | MIT | [source](https://github.com/jmespath/jmespath.py) |
| Python | jieba | 0.42.1 | MIT | [source](https://github.com/fxsjy/jieba) |
| Python | pydantic-settings | 2.9.1 | MIT | [source](https://github.com/pydantic/pydantic-settings) |
| Python | rocketmq-python-client | 5.1.1 | See upstream license |  |
| Python | aliyun-python-sdk-kms | 2.16.5 | Apache | [source](http://develop.aliyun.com/sdk/python) |
| Python | idna | 3.18 | BSD-3-Clause | [source](https://github.com/kjd/idna/blob/master/HISTORY.md) |
| Python | crcmod | 1.7 | MIT | [source](http://crcmod.sourceforge.net/) |
| Python | typing_extensions | 4.16.0 | PSF-2.0 | [source](https://github.com/python/typing_extensions/issues) |
| Python | opentelemetry-api | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api) |
| Python | requests | 2.34.2 | Apache-2.0 | [source](https://requests.readthedocs.io) |
| Python | click | 8.4.2 | BSD-3-Clause | [source](https://click.palletsprojects.com/page/changes/) |
| Python | altgraph | 0.17.5 | MIT | [source](https://altgraph.readthedocs.io) |
| Python | grpcio | 1.82.1 | Apache-2.0 | [source](https://grpc.io) |
| Python | python-dotenv | 1.2.2 | BSD-3-Clause | [source](https://github.com/theskumar/python-dotenv) |
| Python | opentelemetry-exporter-otlp | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp) |
| Python | urllib3 | 2.7.0 | MIT | [source](https://github.com/urllib3/urllib3/blob/main/CHANGES.rst) |
| Python | protobuf | 7.35.1 | 3-Clause BSD License | [source](https://developers.google.com/protocol-buffers/) |
| Python | opentelemetry-sdk | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-sdk) |
| Python | grpcio-tools | 1.82.1 | Apache-2.0 | [source](https://grpc.io) |
| Python | starlette | 0.46.2 | BSD-3-Clause | [source](https://github.com/encode/starlette) |
| Python | pydantic | 2.13.4 | MIT | [source](https://github.com/pydantic/pydantic) |
| Python | setuptools | 84.2.1 | MIT | [source](https://github.com/pypa/setuptools) |
| Python | annotated-types | 0.7.0 | See upstream license | [source](https://github.com/annotated-types/annotated-types) |
| Python | opentelemetry-exporter-otlp-proto-grpc | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-grpc) |
| Python | googleapis-common-protos | 1.75.0 | Apache 2.0 | [source](https://github.com/googleapis/google-cloud-python/tree/main/packages/googleapis-common-protos) |
| Python | cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause | [source](https://cryptography.io/en/latest/changelog/) |
| Python | charset-normalizer | 3.4.9 | MIT | [source](https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md) |
| Python | cffi | 2.1.0 | MIT-0 | [source](https://cffi.readthedocs.io/) |
| Python | opentelemetry-semantic-conventions | 0.64b0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-semantic-conventions) |
| Python | typing-inspection | 0.4.2 | MIT | [source](https://github.com/pydantic/typing-inspection) |
| Python | opentelemetry-exporter-otlp-proto-common | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-common) |
| Python | pycparser | 3.0 | BSD-3-Clause | [source](https://github.com/eliben/pycparser) |
| Python | opentelemetry-proto | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-proto) |
| Python | opentelemetry-exporter-otlp-proto-http | 1.43.0 | Apache-2.0 | [source](https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-http) |
| Python | pydantic_core | 2.46.4 | MIT | [source](https://github.com/pydantic/pydantic) |
| Node/assets | @tabler/icons | 3.35.0 | MIT | [source](https://github.com/tabler/tabler-icons.git) |
| Node/assets | @tauri-apps/cli | 2.11.2 | Apache-2.0 OR MIT | [source](https://github.com/tauri-apps/tauri.git) |
