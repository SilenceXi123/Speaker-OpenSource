from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote
from urllib.parse import urlparse
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, parse, tostring

from fastapi import HTTPException

from app.schemas.subtitle import SubtitleCue


DEFAULT_FRAME_RATE = 25
NTSC_29_97_FRAME_RATE = Fraction(30000, 1001)
XML_VERSION = '4'
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
DEFAULT_SUBTITLE_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / 'data' / 'premiere_subtitle_template.xml'
PPRO_TICKS_PER_SECOND = 12_700_800_000_000
SHORT_VISUAL_GAP_JOIN_FRAMES = 10
GRAPHIC_SUBTITLE_HANDLE_SECONDS = 60


@dataclass
class VisualCueSpan:
    cue: SubtitleCue
    start_frame: int
    end_frame: int

    @property
    def duration_frames(self) -> int:
        return max(self.end_frame - self.start_frame, 1)


class PremiereSubtitleFrameCollisionError(ValueError):
    def __init__(
        self,
        *,
        previous_cue_index: int,
        cue_index: int,
        previous_end_frame: int,
        start_frame: int,
        frame_rate: str,
    ) -> None:
        super().__init__('Subtitle cues overlap after frame snapping')
        self.previous_cue_index = previous_cue_index
        self.cue_index = cue_index
        self.previous_end_frame = previous_end_frame
        self.start_frame = start_frame
        self.frame_rate = frame_rate


def _build_premiere_sequence_scaffold(
    *,
    sequence_id: str,
    sequence_name: str,
    duration_frames: int,
    frame_rate: int | float | str | Fraction,
) -> tuple[Element, Element, Element]:
    root = Element('xmeml', version=XML_VERSION)
    sequence = SubElement(root, 'sequence', id=sequence_id, explodedTracks='true')
    SubElement(sequence, 'uuid').text = str(uuid4()).upper()
    SubElement(sequence, 'name').text = sequence_name
    _append_rate(sequence, frame_rate)
    SubElement(sequence, 'duration').text = str(duration_frames)
    SubElement(sequence, 'in').text = '0'
    SubElement(sequence, 'out').text = str(duration_frames)

    media = SubElement(sequence, 'media')
    video = SubElement(media, 'video')
    format_el = SubElement(video, 'format')
    sample = SubElement(format_el, 'samplecharacteristics')
    _append_rate(sample, frame_rate)
    _append_sample_characteristics(sample)
    return root, media, video


def build_premiere_xml(
    job_id: str,
    source_path: str,
    cues: list[SubtitleCue],
    matched_assets: dict[str, dict],
    source_media_kind: str = 'video',
    frame_rate: int | float | str | Fraction = DEFAULT_FRAME_RATE,
    subtitle_mode: Literal['sidecar', 'text_track', 'graphicandtype'] = 'sidecar',
    subtitle_template_path: Optional[str] = None,
    subtitle_backplate_asset: Optional[dict] = None,
) -> str:
    frame_rate_value = _normalize_frame_rate(frame_rate)
    visual_spans = _build_visual_cue_spans(cues, frame_rate)
    duration_frames = max(
        (visual_spans[-1].end_frame if visual_spans else 0) + _seconds_to_frames(2, frame_rate_value),
        _seconds_to_frames(4, frame_rate_value),
    )

    root, media, video = _build_premiere_sequence_scaffold(
        sequence_id=f'sequence-{job_id}',
        sequence_name=f'Speaker Export {job_id}',
        duration_frames=duration_frames,
        frame_rate=frame_rate,
    )

    source_file = _build_file_element(
        file_id=f'file-source-{job_id}',
        name=_reference_name(source_path, fallback_name=f'source-{source_media_kind}'),
        path_url=_normalize_media_reference(source_path),
        duration_frames=duration_frames,
        media_kind=source_media_kind,
        frame_rate=frame_rate,
    )

    if source_media_kind == 'video':
        source_track = SubElement(video, 'track')
        _apply_premiere_track_attributes(source_track, targeted=True)
        _append_clipitem(
            track=source_track,
            clip_id=f'clip-video-{job_id}',
            name='source-video',
            start=0,
            end=duration_frames,
            in_point=0,
            out_point=duration_frames,
            file_ref=source_file,
            source_track_media_type='video',
            frame_rate=frame_rate,
        )

    if cues:
        if subtitle_backplate_asset:
            _append_subtitle_backplate_track(video, visual_spans, subtitle_backplate_asset, matched_assets, frame_rate)
        if subtitle_mode == 'text_track':
            _append_subtitle_text_track(video, visual_spans, frame_rate, subtitle_template_path)
        elif subtitle_mode == 'graphicandtype':
            _append_subtitle_graphic_track(video, visual_spans, frame_rate)

    if matched_assets:
        overlay_track = SubElement(video, 'track')
        _apply_premiere_track_attributes(overlay_track)
        for index, span in enumerate((item for item in visual_spans if item.cue.clean_text), start=1):
            cue = span.cue
            asset = matched_assets.get(cue.speaker_name)
            if not asset:
                continue
            start_frame = span.start_frame
            end_frame = span.end_frame
            duration = span.duration_frames
            in_point, out_point, source_duration = _visual_handle_clip_timing(duration, frame_rate)
            asset_file = _build_file_element(
                file_id=f'file-overlay-{index}',
                name=asset['file_name'],
                path_url=_normalize_media_reference(resolve_lower_third_reference(asset)),
                duration_frames=source_duration,
                media_kind='still',
                frame_rate=frame_rate,
            )
            _append_clipitem(
                track=overlay_track,
                clip_id=f'clip-overlay-{index}',
                name=asset['speaker_name'],
                start=start_frame,
                end=end_frame,
                in_point=in_point,
                out_point=out_point,
                file_ref=asset_file,
                source_track_media_type='video',
                frame_rate=frame_rate,
                include_premiere_ticks=True,
            )

    audio = SubElement(media, 'audio')
    audio_track = SubElement(audio, 'track')
    _apply_premiere_track_attributes(audio_track)
    _append_audio_clipitem(
        track=audio_track,
        clip_id=f'clip-audio-{job_id}',
        name=f'source-{source_media_kind}-audio',
        start=0,
        end=duration_frames,
        in_point=0,
        out_point=duration_frames,
        file_ref=source_file,
        source_media_kind=source_media_kind,
        frame_rate=frame_rate,
    )

    xml_body = tostring(root, encoding='unicode')
    return f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE xmeml>\n{xml_body}'


def build_premiere_subtitle_xml(
    sequence_name: str,
    cues: list[SubtitleCue],
    frame_rate: int | float | str | Fraction = DEFAULT_FRAME_RATE,
) -> str:
    frame_rate_value = _normalize_frame_rate(frame_rate)
    visual_spans = _build_visual_cue_spans(cues, frame_rate, join_short_gaps=False)
    for previous, current in zip(visual_spans, visual_spans[1:]):
        if current.start_frame < previous.end_frame:
            raise PremiereSubtitleFrameCollisionError(
                previous_cue_index=previous.cue.index,
                cue_index=current.cue.index,
                previous_end_frame=previous.end_frame,
                start_frame=current.start_frame,
                frame_rate=str(frame_rate),
            )
    duration_frames = max(
        (visual_spans[-1].end_frame if visual_spans else 0) + _seconds_to_frames(2, frame_rate_value),
        _seconds_to_frames(4, frame_rate_value),
    )
    safe_name = str(sequence_name or 'subtitles').strip() or 'subtitles'

    root, _, video = _build_premiere_sequence_scaffold(
        sequence_id='sequence-srt-subtitles',
        sequence_name=safe_name,
        duration_frames=duration_frames,
        frame_rate=frame_rate,
    )
    _append_subtitle_graphic_track(video, visual_spans, frame_rate)

    xml_body = tostring(root, encoding='unicode')
    return f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE xmeml>\n{xml_body}'


def build_visual_aligned_subtitle_cues(
    cues: list[SubtitleCue],
    frame_rate: int | float | str | Fraction = DEFAULT_FRAME_RATE,
) -> list[SubtitleCue]:
    aligned_cues: list[SubtitleCue] = []
    for span in _build_visual_cue_spans(cues, frame_rate):
        cue = span.cue.model_copy(deep=True)
        cue.start_ms = _frames_to_ms(span.start_frame, frame_rate)
        cue.end_ms = max(_frames_to_ms(span.end_frame, frame_rate), cue.start_ms + 1)
        aligned_cues.append(cue)
    return aligned_cues


def _append_rate(parent: Element, frame_rate: int | float | str | Fraction) -> None:
    timebase, ntsc = _frame_rate_xml_parts(frame_rate)
    rate = SubElement(parent, 'rate')
    SubElement(rate, 'timebase').text = str(timebase)
    SubElement(rate, 'ntsc').text = 'TRUE' if ntsc else 'FALSE'


def _append_sample_characteristics(parent: Element) -> None:
    SubElement(parent, 'width').text = str(FRAME_WIDTH)
    SubElement(parent, 'height').text = str(FRAME_HEIGHT)
    SubElement(parent, 'anamorphic').text = 'FALSE'
    SubElement(parent, 'pixelaspectratio').text = 'square'
    SubElement(parent, 'fielddominance').text = 'none'


def _apply_premiere_track_attributes(track: Element, targeted: bool = False) -> None:
    track.set('TL.SQTrackShy', '0')
    track.set('TL.SQTrackExpandedHeight', '25')
    track.set('TL.SQTrackExpanded', '0')
    track.set('MZ.TrackTargeted', '1' if targeted else '0')


def _build_file_element(file_id: str, name: str, path_url: str, duration_frames: int, media_kind: str, frame_rate: int) -> Element:
    file_el = Element('file', id=file_id)
    SubElement(file_el, 'name').text = name
    SubElement(file_el, 'pathurl').text = path_url
    SubElement(file_el, 'duration').text = str(duration_frames)
    _append_rate(file_el, frame_rate)
    media = SubElement(file_el, 'media')
    video = SubElement(media, 'video')
    sample = SubElement(video, 'samplecharacteristics')
    _append_rate(sample, frame_rate)
    _append_sample_characteristics(sample)
    if media_kind == 'video':
        audio = SubElement(media, 'audio')
        audio_track = SubElement(audio, 'track')
        SubElement(audio_track, 'channelcount').text = '2'
    elif media_kind == 'audio':
        audio = SubElement(media, 'audio')
        audio_track = SubElement(audio, 'track')
        SubElement(audio_track, 'channelcount').text = '2'
    return file_el


def _reference_name(value: str, fallback_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme:
        return Path(parsed.path).name or fallback_name
    return Path(value).name or fallback_name


def _normalize_media_reference(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme:
        return value
    path = Path(value).expanduser().resolve()
    return f'file://{quote(path.as_posix())}'


def resolve_lower_third_reference(asset: dict) -> str:
    return _resolve_asset_reference(asset, label='人名条')


def _resolve_asset_reference(asset: dict, label: str) -> str:
    for key in ('export_path', 'local_path', 'public_url'):
        value = str(asset.get(key) or '').strip()
        if value:
            return value
    raise HTTPException(
        status_code=400,
        detail=f"{label}缺少可用于 XML 导出的路径：{asset.get('file_name') or asset.get('speaker_name') or 'unknown'}",
    )


def _append_clipitem(
    track: Element,
    clip_id: str,
    name: str,
    start: int,
    end: int,
    in_point: int,
    out_point: int,
    file_ref: Element,
    source_track_media_type: Optional[str] = None,
    track_index: int = 1,
    frame_rate: int = DEFAULT_FRAME_RATE,
    include_premiere_ticks: bool = False,
) -> None:
    clipitem = SubElement(track, 'clipitem', id=clip_id)
    SubElement(clipitem, 'name').text = name
    SubElement(clipitem, 'duration').text = str(max(end - start, 1))
    _append_rate(clipitem, frame_rate)
    SubElement(clipitem, 'start').text = str(start)
    SubElement(clipitem, 'end').text = str(end)
    SubElement(clipitem, 'in').text = str(in_point)
    SubElement(clipitem, 'out').text = str(out_point)
    if include_premiere_ticks:
        ticks_per_frame = _ppro_ticks_per_frame(frame_rate)
        SubElement(clipitem, 'pproTicksIn').text = str(in_point * ticks_per_frame)
        SubElement(clipitem, 'pproTicksOut').text = str(out_point * ticks_per_frame)
    SubElement(clipitem, 'enabled').text = 'TRUE'
    clipitem.append(file_ref)
    if source_track_media_type:
        sourcetrack = SubElement(clipitem, 'sourcetrack')
        SubElement(sourcetrack, 'mediatype').text = source_track_media_type
        SubElement(sourcetrack, 'trackindex').text = str(track_index)


def _append_audio_clipitem(
    track: Element,
    clip_id: str,
    name: str,
    start: int,
    end: int,
    in_point: int,
    out_point: int,
    file_ref: Element,
    source_media_kind: str,
    frame_rate: int = DEFAULT_FRAME_RATE,
) -> None:
    clipitem = SubElement(track, 'clipitem', id=clip_id)
    SubElement(clipitem, 'name').text = name
    SubElement(clipitem, 'duration').text = str(max(end - start, 1))
    _append_rate(clipitem, frame_rate)
    SubElement(clipitem, 'start').text = str(start)
    SubElement(clipitem, 'end').text = str(end)
    SubElement(clipitem, 'in').text = str(in_point)
    SubElement(clipitem, 'out').text = str(out_point)
    SubElement(clipitem, 'enabled').text = 'TRUE'
    clipitem.append(file_ref)
    sourcetrack = SubElement(clipitem, 'sourcetrack')
    SubElement(sourcetrack, 'mediatype').text = 'audio'
    SubElement(sourcetrack, 'trackindex').text = '1'
    if source_media_kind == 'audio':
        labels = SubElement(clipitem, 'labels')
        SubElement(labels, 'label2').text = 'Lavender'


def _append_subtitle_text_track(
    video: Element,
    visual_spans: list[VisualCueSpan],
    frame_rate: int,
    subtitle_template_path: Optional[str] = None,
) -> None:
    track = SubElement(video, 'track')
    _apply_premiere_track_attributes(track)
    template_generator = _load_subtitle_generator_template(subtitle_template_path)
    for span in visual_spans:
        cue = span.cue
        start_frame = span.start_frame
        end_frame = span.end_frame
        duration_frames = span.duration_frames
        if template_generator is not None:
            generator = deepcopy(template_generator)
            generator.attrib['id'] = f'clip-subtitle-{cue.index}'
            _set_or_add_child_text(generator, 'name', f'subtitle-{cue.index}')
            _set_or_add_child_text(generator, 'duration', str(duration_frames))
            _ensure_rate(generator, frame_rate)
            _set_or_add_child_text(generator, 'start', str(start_frame))
            _set_or_add_child_text(generator, 'end', str(end_frame))
            _set_or_add_child_text(generator, 'in', '0')
            _set_or_add_child_text(generator, 'out', str(duration_frames))
            _set_or_add_child_text(generator, 'enabled', 'TRUE')
            _set_generator_text_value(generator, cue.clean_text)
            track.append(generator)
            continue

        generator = SubElement(track, 'generatoritem', id=f'clip-subtitle-{cue.index}')
        SubElement(generator, 'name').text = f'subtitle-{cue.index}'
        SubElement(generator, 'duration').text = str(duration_frames)
        _append_rate(generator, frame_rate)
        SubElement(generator, 'start').text = str(start_frame)
        SubElement(generator, 'end').text = str(end_frame)
        SubElement(generator, 'in').text = '0'
        SubElement(generator, 'out').text = str(duration_frames)
        SubElement(generator, 'enabled').text = 'TRUE'
        effect = SubElement(generator, 'effect')
        SubElement(effect, 'name').text = 'Outline Text'
        SubElement(effect, 'effectid').text = 'Outline Text'
        SubElement(effect, 'effectcategory').text = 'Text'
        SubElement(effect, 'effecttype').text = 'generator'
        SubElement(effect, 'mediatype').text = 'video'
        _append_text_parameter(effect, 'str', 'Text', cue.clean_text)
        _append_text_parameter(effect, 'font', 'Font', 'Helvetica')
        _append_text_parameter(effect, 'fontstyle', 'Style', 'Plain')
        _append_text_parameter(effect, 'fontsize', 'Size', '24')
        _append_color_parameter(effect, 'textcolor', 'Text Color', 255, 235, 0, 255)
        _append_color_parameter(effect, 'linecolor', 'Line Color', 0, 0, 0, 255)
        _append_text_parameter(effect, 'linewidth', 'Line Width', '3')
        _append_text_parameter(effect, 'linesoftness', 'Line Softness', '50')


def _append_subtitle_graphic_track(video: Element, visual_spans: list[VisualCueSpan], frame_rate: int) -> None:
    track = SubElement(video, 'track')
    _apply_premiere_track_attributes(track)
    for span in visual_spans:
        cue = span.cue
        start_frame = span.start_frame
        end_frame = span.end_frame
        duration_frames = span.duration_frames
        source_handle_frames = _graphic_subtitle_handle_frames(frame_rate)
        in_point = source_handle_frames
        out_point = in_point + duration_frames
        source_duration_frames = out_point + source_handle_frames
        clipitem = SubElement(track, 'clipitem', id=f'clipitem-subtitle-{cue.index}')
        SubElement(clipitem, 'masterclipid').text = f'masterclip-subtitle-{cue.index}'
        SubElement(clipitem, 'name').text = _build_subtitle_clip_name(cue.clean_text)
        SubElement(clipitem, 'enabled').text = 'TRUE'
        SubElement(clipitem, 'duration').text = str(duration_frames)
        _append_rate(clipitem, frame_rate)
        SubElement(clipitem, 'start').text = str(start_frame)
        SubElement(clipitem, 'end').text = str(end_frame)
        SubElement(clipitem, 'in').text = str(in_point)
        SubElement(clipitem, 'out').text = str(out_point)
        SubElement(clipitem, 'pproTicksIn').text = str(in_point * _ppro_ticks_per_frame(frame_rate))
        SubElement(clipitem, 'pproTicksOut').text = str(out_point * _ppro_ticks_per_frame(frame_rate))
        SubElement(clipitem, 'alphatype').text = 'straight'
        SubElement(clipitem, 'pixelaspectratio').text = 'square'
        SubElement(clipitem, 'anamorphic').text = 'FALSE'
        clipitem.append(_build_graphicandtype_file_element(cue.index, frame_rate, source_duration_frames))
        clipitem.append(_build_graphicandtype_filter(cue.clean_text))


def _append_subtitle_backplate_track(
    video: Element,
    visual_spans: list[VisualCueSpan],
    asset_config: dict,
    matched_assets: dict[str, dict],
    frame_rate: int,
) -> None:
    active_items = [
        (span, asset)
        for span in visual_spans
        if span.cue.clean_text
        for asset in [_select_subtitle_backplate_asset(asset_config, span, matched_assets)]
        if asset
    ]
    if not active_items:
        return
    track = SubElement(video, 'track')
    _apply_premiere_track_attributes(track)
    for index, (span, asset) in enumerate(active_items, start=1):
        file_name = str(asset.get('file_name') or asset.get('object_key') or '唱词条底.png').strip() or '唱词条底.png'
        path_url = _normalize_media_reference(_resolve_asset_reference(asset, label='唱词条底图'))
        duration = span.duration_frames
        in_point, out_point, source_duration = _visual_handle_clip_timing(duration, frame_rate)
        asset_file = _build_file_element(
            file_id=f'file-backplate-{index}',
            name=file_name,
            path_url=path_url,
            duration_frames=source_duration,
            media_kind='still',
            frame_rate=frame_rate,
        )
        _append_clipitem(
            track=track,
            clip_id=f'clip-backplate-{index}',
            name=file_name,
            start=span.start_frame,
            end=span.end_frame,
            in_point=in_point,
            out_point=out_point,
            file_ref=asset_file,
            source_track_media_type='video',
            frame_rate=frame_rate,
            include_premiere_ticks=True,
        )


def _select_subtitle_backplate_asset(
    asset_config: dict,
    span: VisualCueSpan,
    matched_assets: dict[str, dict],
) -> Optional[dict]:
    role_assets = _normalize_subtitle_backplate_assets(asset_config)
    if not role_assets:
        return None
    role = 'with_speaker' if matched_assets.get(span.cue.speaker_name) else 'without_speaker'
    return role_assets.get(role)


def _normalize_subtitle_backplate_assets(asset_config: dict) -> dict[str, dict]:
    from app.services.jobs import extract_subtitle_backplate_role_assets

    return extract_subtitle_backplate_role_assets(asset_config)


def _build_subtitle_clip_name(text: str) -> str:
    normalized = ' '.join(str(text or '').split()).strip()
    return normalized or '图形'


def _graphic_subtitle_handle_frames(frame_rate: int | float | str | Fraction) -> int:
    return _seconds_to_frames(GRAPHIC_SUBTITLE_HANDLE_SECONDS, frame_rate)


def _visual_handle_clip_timing(duration_frames: int, frame_rate: int | float | str | Fraction) -> tuple[int, int, int]:
    source_handle_frames = _graphic_subtitle_handle_frames(frame_rate)
    in_point = source_handle_frames
    out_point = in_point + max(int(duration_frames), 1)
    source_duration_frames = out_point + source_handle_frames
    return in_point, out_point, source_duration_frames


def _load_subtitle_generator_template(subtitle_template_path: Optional[str] = None) -> Optional[Element]:
    template_path = Path(subtitle_template_path) if subtitle_template_path else DEFAULT_SUBTITLE_TEMPLATE_PATH
    if not template_path.exists():
        return None
    tree = parse(template_path)
    return tree.find('.//generatoritem')


def _set_or_add_child_text(parent: Element, tag_name: str, value: str) -> None:
    child = parent.find(tag_name)
    if child is None:
        child = SubElement(parent, tag_name)
    child.text = value


def _ensure_rate(parent: Element, frame_rate: int | float | str | Fraction) -> None:
    timebase, ntsc = _frame_rate_xml_parts(frame_rate)
    rate = parent.find('rate')
    if rate is None:
        _append_rate(parent, frame_rate)
        return
    _set_or_add_child_text(rate, 'timebase', str(timebase))
    _set_or_add_child_text(rate, 'ntsc', 'TRUE' if ntsc else 'FALSE')


def _set_generator_text_value(generator: Element, text: str) -> None:
    for parameter in generator.findall('.//parameter'):
        parameter_name = (parameter.findtext('name') or '').strip()
        parameter_id = (parameter.findtext('parameterid') or '').strip()
        if parameter_name == 'Text' or parameter_id == 'str':
            _set_or_add_child_text(parameter, 'value', text)
            return
    effect = generator.find('effect')
    if effect is None:
        effect = SubElement(generator, 'effect')
        _set_or_add_child_text(effect, 'name', 'Outline Text')
        _set_or_add_child_text(effect, 'effectid', 'Outline Text')
        _set_or_add_child_text(effect, 'effectcategory', 'Text')
        _set_or_add_child_text(effect, 'effecttype', 'generator')
        _set_or_add_child_text(effect, 'mediatype', 'video')
    _append_text_parameter(effect, 'str', 'Text', text)


def _append_text_parameter(effect: Element, parameter_id: str, name: str, value: str) -> None:
    parameter = SubElement(effect, 'parameter')
    SubElement(parameter, 'parameterid').text = parameter_id
    SubElement(parameter, 'name').text = name
    SubElement(parameter, 'value').text = value


def _append_color_parameter(effect: Element, parameter_id: str, name: str, red: int, green: int, blue: int, alpha: int = 255) -> None:
    parameter = SubElement(effect, 'parameter')
    SubElement(parameter, 'parameterid').text = parameter_id
    SubElement(parameter, 'name').text = name
    value = SubElement(parameter, 'value')
    SubElement(value, 'alpha').text = str(alpha)
    SubElement(value, 'red').text = str(red)
    SubElement(value, 'green').text = str(green)
    SubElement(value, 'blue').text = str(blue)


def _build_graphicandtype_file_element(index: int, frame_rate: int | float | str | Fraction, duration_frames: int) -> Element:
    file_el = Element('file', id=f'file-subtitle-{index}')
    SubElement(file_el, 'name').text = 'Graphic'
    SubElement(file_el, 'mediaSource').text = 'GraphicAndType'
    SubElement(file_el, 'duration').text = str(duration_frames)
    _append_rate(file_el, frame_rate)
    timecode = SubElement(file_el, 'timecode')
    _append_rate(timecode, frame_rate)
    separator = ';' if _uses_drop_frame_display(frame_rate) else ':'
    SubElement(timecode, 'string').text = f'00{separator}00{separator}00{separator}00'
    SubElement(timecode, 'frame').text = '0'
    SubElement(timecode, 'displayformat').text = 'DF' if _uses_drop_frame_display(frame_rate) else 'NDF'
    media = SubElement(file_el, 'media')
    video = SubElement(media, 'video')
    sample = SubElement(video, 'samplecharacteristics')
    _append_rate(sample, frame_rate)
    _append_sample_characteristics(sample)
    return file_el


def _build_graphicandtype_filter(text: str) -> Element:
    filter_el = Element('filter')
    effect = SubElement(filter_el, 'effect')
    SubElement(effect, 'name').text = text
    SubElement(effect, 'effectid').text = 'GraphicAndType'
    SubElement(effect, 'effectcategory').text = 'graphic'
    SubElement(effect, 'effecttype').text = 'filter'
    SubElement(effect, 'mediatype').text = 'video'
    SubElement(effect, 'pproBypass').text = 'false'
    SubElement(effect, 'pproExpanded').text = 'true'

    source_text = SubElement(effect, 'parameter', authoringApp='PremierePro')
    SubElement(source_text, 'parameterid').text = '1'
    SubElement(source_text, 'name').text = 'Source Text'
    SubElement(source_text, 'hash').text = str(uuid4()).upper()
    SubElement(source_text, 'value').text = _encode_graphicandtype_source_text_payload(text)

    transform = SubElement(source_text, 'parameter', authoringApp='PremierePro')
    SubElement(transform, 'parameterid').text = '2'
    SubElement(transform, 'name').text = '变换'
    SubElement(transform, 'IsTimeVarying').text = 'false'
    SubElement(transform, 'ParameterControlType').text = '11'
    SubElement(transform, 'LowerBound').text = 'false'
    SubElement(transform, 'UpperBound').text = 'false'
    SubElement(transform, 'value').text = '0,false,0,0,0,0,0,0'

    position = SubElement(source_text, 'parameter', authoringApp='PremierePro')
    SubElement(position, 'parameterid').text = '3'
    SubElement(position, 'name').text = '位置'
    SubElement(position, 'IsTimeVarying').text = 'false'
    SubElement(position, 'value').text = '0,0.5:0.8796296296296297,0,0,0,0,0,0,5,4,0,0,0,0'

    scale = SubElement(source_text, 'parameter', authoringApp='PremierePro')
    SubElement(scale, 'parameterid').text = '-1'
    SubElement(scale, 'name').text = '缩放'
    SubElement(scale, 'IsTimeVarying').text = 'false'
    SubElement(scale, 'ParameterControlType').text = '2'
    SubElement(scale, 'LowerBound').text = '0'
    SubElement(scale, 'UpperBound').text = '400'
    SubElement(scale, 'value').text = '0,100.,0,0,0,0,0,0'

    horizontal_scale = SubElement(source_text, 'parameter', authoringApp='PremierePro')
    SubElement(horizontal_scale, 'parameterid').text = '-1'
    SubElement(horizontal_scale, 'name').text = '水平缩放'
    SubElement(horizontal_scale, 'IsTimeVarying').text = 'false'
    SubElement(horizontal_scale, 'ParameterControlType').text = '2'
    SubElement(horizontal_scale, 'LowerBound').text = '0'
    SubElement(horizontal_scale, 'UpperBound').text = '400'
    SubElement(horizontal_scale, 'value').text = '0,100.,0,0,0,0,0,0'
    return filter_el


def _encode_graphicandtype_source_text_payload(text: str) -> str:
    payload = {
        'mTextParam': {
            'mAlignment': 2,
            'mDefaultRun': [],
            'mHeight': 0,
            'mHindiDigits': False,
            'mIndic': False,
            'mIsVerticalText': False,
            'mLeading': 0,
            'mLigatures': False,
            'mRTL': False,
            'mShadowAngle': 0.0,
            'mShadowBlur': 0,
            'mShadowColor': 0,
            'mShadowOffset': 0.0,
            'mShadowOpacity': 100,
            'mShadowVisible': False,
            'mStyleSheet': {
                'mBaselineShift': {'mParamValues': [[0, 0]]},
                'mFillColor': {'mParamValues': [[0, 16_777_215]]},
                'mFillOverStroke': {'mParamValues': [[0, True]]},
                'mFillVisible': {'mParamValues': [[0, True]]},
                'mFontName': {'mParamValues': [[0, 'PingFangTC-Regular']]},
                'mFontSize': {'mParamValues': [[0, 48]]},
                'mKerning': {'mParamValues': [[0, 0]]},
                'mStrokeColor': {'mParamValues': [[0, 0]]},
                'mStrokeVisible': {'mParamValues': [[0, True]]},
                'mStrokeWidth': {'mParamValues': [[0, 1.0]]},
                'mText': text,
                'mTracking': {'mParamValues': [[0, 0]]},
                'mTsumi': {'mParamValues': [[0, 0]]},
            },
            'mTabWidth': 400,
            'mWidth': 0,
        },
        'mVersion': 1,
    }
    encoded_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-16le')
    raw = b'\xfc\x06\x00\x00\x00\x00\x00\x00' + encoded_json
    return base64.b64encode(raw).decode('ascii')


def _normalize_frame_rate(frame_rate: int | float | str | Fraction) -> Fraction:
    if isinstance(frame_rate, Fraction):
        return frame_rate if frame_rate > 0 else Fraction(DEFAULT_FRAME_RATE, 1)
    if isinstance(frame_rate, str):
        normalized = frame_rate.strip().lower().replace('fps', '').strip()
        if normalized in {'29.97', '29.970', '29.97002997', '30000/1001'}:
            return NTSC_29_97_FRAME_RATE
        if '/' in normalized:
            numerator, denominator = normalized.split('/', 1)
            try:
                value = Fraction(int(numerator.strip()), int(denominator.strip()))
                return value if value > 0 else Fraction(DEFAULT_FRAME_RATE, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                return Fraction(DEFAULT_FRAME_RATE, 1)
        try:
            value = Fraction(normalized)
            if abs(float(value) - float(NTSC_29_97_FRAME_RATE)) < 0.01:
                return NTSC_29_97_FRAME_RATE
            return value if value > 0 else Fraction(DEFAULT_FRAME_RATE, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            return Fraction(DEFAULT_FRAME_RATE, 1)
    if isinstance(frame_rate, float):
        if abs(frame_rate - float(NTSC_29_97_FRAME_RATE)) < 0.01:
            return NTSC_29_97_FRAME_RATE
        return Fraction(str(frame_rate)).limit_denominator(1001) if frame_rate > 0 else Fraction(DEFAULT_FRAME_RATE, 1)
    try:
        value = int(frame_rate)
    except (TypeError, ValueError):
        value = DEFAULT_FRAME_RATE
    return Fraction(value, 1) if value > 0 else Fraction(DEFAULT_FRAME_RATE, 1)


def _is_ntsc_fraction(frame_rate: int | float | str | Fraction) -> bool:
    return _normalize_frame_rate(frame_rate) == NTSC_29_97_FRAME_RATE


def _frame_rate_xml_parts(frame_rate: int | float | str | Fraction) -> tuple[int, bool]:
    rate = _normalize_frame_rate(frame_rate)
    if rate == NTSC_29_97_FRAME_RATE:
        return 30, True
    if rate.denominator == 1:
        return rate.numerator, False
    return max(int(round(float(rate))), 1), False


def _uses_drop_frame_display(frame_rate: int | float | str | Fraction) -> bool:
    rate = _normalize_frame_rate(frame_rate)
    return rate == NTSC_29_97_FRAME_RATE or rate == Fraction(50, 1)


def _seconds_to_frames(seconds: int | float, frame_rate: int | float | str | Fraction) -> int:
    rate = _normalize_frame_rate(frame_rate)
    value = rate * Fraction(str(seconds))
    return max(_round_fraction_half_down(value), 1)


def _ppro_ticks_per_frame(frame_rate: int | float | str | Fraction) -> int:
    rate = _normalize_frame_rate(frame_rate)
    return max((PPRO_TICKS_PER_SECOND * rate.denominator + rate.numerator // 2) // rate.numerator, 1)


def _build_visual_cue_spans(
    cues: list[SubtitleCue],
    frame_rate: int | float | str | Fraction,
    *,
    join_short_gaps: bool = True,
) -> list[VisualCueSpan]:
    spans = [
        VisualCueSpan(
            cue=cue,
            start_frame=_cue_nearest_frame(cue.start_ms, frame_rate),
            end_frame=max(_cue_nearest_frame(cue.end_ms, frame_rate), _cue_nearest_frame(cue.start_ms, frame_rate) + 1),
        )
        for cue in cues
    ]
    for index in range(len(spans) - 1):
        current = spans[index]
        following = spans[index + 1]
        if current.end_frame > following.start_frame and following.start_frame > current.start_frame:
            current.end_frame = following.start_frame
            continue
        gap_frames = following.start_frame - current.end_frame
        if join_short_gaps and 0 < gap_frames <= SHORT_VISUAL_GAP_JOIN_FRAMES:
            current.end_frame = following.start_frame
    return spans


def _ms_to_frames(ms: int, frame_rate: int | float | str | Fraction) -> int:
    return _cue_nearest_frame(ms, frame_rate)


def _frames_to_ms(frame: int, frame_rate: int | float | str | Fraction) -> int:
    rate = _normalize_frame_rate(frame_rate)
    value = Fraction(max(frame, 0) * 1000 * rate.denominator, rate.numerator)
    return _round_fraction_half_down(value)


def _cue_nearest_frame(ms: int, frame_rate: int | float | str | Fraction) -> int:
    rate = _normalize_frame_rate(frame_rate)
    frame_value = Fraction(max(ms, 0), 1000) * rate
    return max(_round_fraction_half_down(frame_value), 0)


def _round_fraction_half_down(value: Fraction) -> int:
    numerator = value.numerator
    denominator = value.denominator
    whole = numerator // denominator
    remainder = numerator % denominator
    return whole + 1 if remainder * 2 > denominator else whole
