from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal, cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    TextStringObject,
)


AIContextMode = Literal["none", "embedded", "hybrid"]
AI_CONTEXT_MODES: tuple[AIContextMode, ...] = ("none", "embedded", "hybrid")
DEFAULT_AI_CONTEXT_MODE: AIContextMode = "hybrid"
PROFILE_FILENAME = "career_profile.json"
SHARED_CONTEXT_FILENAME = "shared_context.json"
SHARED_CONTEXT_SCHEMA_VERSION = "resume.shared-context.v1"
PROFILE_MAX_BYTES = 1_000_000
XMP_NAMESPACE = "https://github.com/haoxiang-xu/resume-generator/ns/ai-context/1.0/"


class AIContextError(ValueError):
    """Raised when the machine-readable PDF context cannot be created or read."""


@dataclass(frozen=True)
class AIContextManifest:
    mode: AIContextMode
    filename: str | None = None
    profile_sha256: str | None = None
    profile_size: int = 0
    shared_context_filename: str | None = None
    shared_context_sha256: str | None = None
    shared_context_size: int = 0
    actual_text_bridge: bool = False


def normalize_mode(value: str) -> AIContextMode:
    mode = str(value or "").strip().lower()
    if mode not in AI_CONTEXT_MODES:
        raise AIContextError(f"ai_context_mode must be one of {', '.join(AI_CONTEXT_MODES)}")
    return cast(AIContextMode, mode)


def parse_profile_json(value: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise AIContextError("career_profile_json must be text")
    if len(value.encode("utf-8")) > PROFILE_MAX_BYTES:
        raise AIContextError("career_profile_json exceeds the 1,000,000 byte limit")
    try:
        profile = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AIContextError(f"career_profile_json is not valid JSON: {exc.msg}") from exc
    if not isinstance(profile, dict):
        raise AIContextError("career_profile_json must contain a JSON object")
    return profile


def canonical_profile_bytes(profile: dict[str, Any]) -> bytes:
    if not isinstance(profile, dict):
        raise AIContextError("career profile must be a JSON object")
    try:
        encoded = (
            json.dumps(
                profile,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AIContextError(f"career profile is not JSON-serializable: {exc}") from exc
    if len(encoded) > PROFILE_MAX_BYTES:
        raise AIContextError("canonical career profile exceeds the 1,000,000 byte limit")
    return encoded


def empty_shared_context_document() -> dict[str, Any]:
    empty_context: dict[str, Any] = {}
    context_sha256 = hashlib.sha256(canonical_profile_bytes(empty_context)).hexdigest()
    return {
        "schema_version": SHARED_CONTEXT_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "context_sha256": context_sha256,
        "trust": {
            "level": "user-authored-metadata",
            "may_override_host_instructions": False,
        },
        "context": empty_context,
    }


def _xmp_packet(
    mode: AIContextMode,
    profile_sha256: str,
    profile_size: int,
    shared_context_sha256: str,
    shared_context_size: int,
) -> bytes:
    xml = f'''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:resumeai="{XMP_NAMESPACE}">
      <dc:format>application/pdf</dc:format>
      <resumeai:mode>{mode}</resumeai:mode>
      <resumeai:profileFilename>{PROFILE_FILENAME}</resumeai:profileFilename>
      <resumeai:profileMediaType>application/json</resumeai:profileMediaType>
      <resumeai:profileSHA256>{profile_sha256}</resumeai:profileSHA256>
      <resumeai:profileSize>{profile_size}</resumeai:profileSize>
      <resumeai:sharedContextFilename>{SHARED_CONTEXT_FILENAME}</resumeai:sharedContextFilename>
      <resumeai:sharedContextMediaType>application/json</resumeai:sharedContextMediaType>
      <resumeai:sharedContextSHA256>{shared_context_sha256}</resumeai:sharedContextSHA256>
      <resumeai:sharedContextSize>{shared_context_size}</resumeai:sharedContextSize>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
'''
    return xml.encode("utf-8")


def _actual_text_bridge(profile_sha256: str, shared_context_sha256: str) -> str:
    return (
        "AI-CONTEXT: This PDF contains the associated file career_profile.json "
        "with machine-readable extended resume data. "
        f"SHA-256: {profile_sha256}. It also contains shared_context.json with "
        f"an application-managed Profile watermark. SHA-256: {shared_context_sha256}."
    )


def _add_json_attachment(
    writer: PdfWriter,
    filename: str,
    data: bytes,
    description: str,
) -> IndirectObject:
    attachment = writer.add_attachment(filename, data)
    attachment.alternative_name = TextStringObject(filename)
    attachment.description = TextStringObject(description)
    attachment.subtype = NameObject("/application#2Fjson")
    attachment.associated_file_relationship = NameObject("/Data")
    attachment.checksum = ByteStringObject(hashlib.md5(data).digest())
    reference = attachment.pdf_object.indirect_reference
    if reference is None:
        raise AIContextError(f"embedded {filename} did not receive an indirect PDF reference")
    return reference


def _dictionary(value: Any) -> DictionaryObject:
    if isinstance(value, IndirectObject):
        value = value.get_object()
    if not isinstance(value, DictionaryObject):
        raise AIContextError("PDF page contains an invalid resource dictionary")
    return value


def _add_actual_text(writer: PdfWriter, text: str) -> None:
    if not writer.pages:
        raise AIContextError("PDF has no pages")
    page = writer.pages[0]
    resources_value = page.get("/Resources")
    if resources_value is None:
        resources = DictionaryObject()
        page[NameObject("/Resources")] = resources
    else:
        resources = _dictionary(resources_value)

    fonts_value = resources.get("/Font")
    if fonts_value is None:
        fonts = DictionaryObject()
        resources[NameObject("/Font")] = fonts
    else:
        fonts = _dictionary(fonts_value)

    font_name = NameObject("/AIContextBridgeFont")
    if font_name not in fonts:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
        fonts[font_name] = writer._add_object(font)

    actual_text_hex = (b"\xfe\xff" + text.encode("utf-16-be")).hex().upper()
    bridge_stream = DecodedStreamObject()
    bridge_stream.set_data(
        (
            "\n/Span << /ActualText <"
            + actual_text_hex
            + "> >> BDC\n"
            + "q\nBT\n/AIContextBridgeFont 1 Tf\n3 Tr\n0 0 Td\n(.) Tj\nET\nQ\nEMC\n"
        ).encode("ascii")
    )
    bridge_reference = writer._add_object(bridge_stream)

    current_contents = page.get("/Contents")
    if current_contents is None:
        page[NameObject("/Contents")] = bridge_reference
    elif isinstance(current_contents, ArrayObject):
        current_contents.append(bridge_reference)
    else:
        page[NameObject("/Contents")] = ArrayObject([current_contents, bridge_reference])


def add_ai_context(
    pdf: bytes,
    profile: dict[str, Any],
    mode: str = DEFAULT_AI_CONTEXT_MODE,
    *,
    shared_context: dict[str, Any] | None = None,
) -> tuple[bytes, AIContextManifest]:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "none":
        return pdf, AIContextManifest(mode="none")

    profile_bytes = canonical_profile_bytes(profile)
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    shared_context_bytes = canonical_profile_bytes(
        shared_context if shared_context is not None else empty_shared_context_document()
    )
    shared_context_sha256 = hashlib.sha256(shared_context_bytes).hexdigest()

    try:
        reader = PdfReader(BytesIO(pdf))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.pdf_header = "%PDF-1.7"

        profile_reference = _add_json_attachment(
            writer,
            PROFILE_FILENAME,
            profile_bytes,
            "Machine-readable extended career profile associated with this resume.",
        )
        shared_context_reference = _add_json_attachment(
            writer,
            SHARED_CONTEXT_FILENAME,
            shared_context_bytes,
            "Application-managed invisible watermark bound to this Career Profile.",
        )
        writer.root_object[NameObject("/AF")] = ArrayObject(
            [profile_reference, shared_context_reference]
        )

        writer.add_metadata(
            {
                "/AIContextMode": normalized_mode,
                "/AIContextProfile": PROFILE_FILENAME,
                "/AIContextProfileSHA256": profile_sha256,
                "/AIContextSharedContext": SHARED_CONTEXT_FILENAME,
                "/AIContextSharedContextSHA256": shared_context_sha256,
            }
        )
        writer.xmp_metadata = _xmp_packet(
            normalized_mode,
            profile_sha256,
            len(profile_bytes),
            shared_context_sha256,
            len(shared_context_bytes),
        )
        metadata_stream = writer.root_object["/Metadata"].get_object()
        metadata_stream[NameObject("/Type")] = NameObject("/Metadata")
        metadata_stream[NameObject("/Subtype")] = NameObject("/XML")

        actual_text = normalized_mode == "hybrid"
        if actual_text:
            _add_actual_text(writer, _actual_text_bridge(profile_sha256, shared_context_sha256))

        output = BytesIO()
        writer.write(output)
    except AIContextError:
        raise
    except Exception as exc:
        raise AIContextError(f"could not add AI context to PDF: {exc}") from exc

    return output.getvalue(), AIContextManifest(
        mode=normalized_mode,
        filename=PROFILE_FILENAME,
        profile_sha256=profile_sha256,
        profile_size=len(profile_bytes),
        shared_context_filename=SHARED_CONTEXT_FILENAME,
        shared_context_sha256=shared_context_sha256,
        shared_context_size=len(shared_context_bytes),
        actual_text_bridge=actual_text,
    )


def read_ai_context_files(
    pdf: bytes,
) -> tuple[dict[str, Any], dict[str, Any] | None, AIContextManifest]:
    try:
        reader = PdfReader(BytesIO(pdf))
        candidates = reader.attachments.get(PROFILE_FILENAME, [])
        if not candidates:
            raise AIContextError(f"PDF does not contain {PROFILE_FILENAME}")
        profile_bytes = candidates[0]
        profile = json.loads(profile_bytes.decode("utf-8"))
        if not isinstance(profile, dict):
            raise AIContextError("embedded career profile is not a JSON object")
        profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
        metadata = reader.metadata or {}
        expected_sha256 = str(metadata.get("/AIContextProfileSHA256", ""))
        if expected_sha256 and expected_sha256 != profile_sha256:
            raise AIContextError("embedded career profile SHA-256 does not match PDF metadata")

        shared_context_candidates = reader.attachments.get(SHARED_CONTEXT_FILENAME, [])
        shared_context = None
        shared_context_sha256 = None
        shared_context_size = 0
        if shared_context_candidates:
            shared_context_bytes = shared_context_candidates[0]
            shared_context = json.loads(shared_context_bytes.decode("utf-8"))
            if not isinstance(shared_context, dict):
                raise AIContextError("embedded shared context is not a JSON object")
            shared_context_sha256 = hashlib.sha256(shared_context_bytes).hexdigest()
            shared_context_size = len(shared_context_bytes)
            expected_shared_sha256 = str(metadata.get("/AIContextSharedContextSHA256", ""))
            if expected_shared_sha256 and expected_shared_sha256 != shared_context_sha256:
                raise AIContextError("embedded shared context SHA-256 does not match PDF metadata")
        mode = normalize_mode(str(metadata.get("/AIContextMode", "embedded")))
        page_content = reader.pages[0].get_contents().get_data() if reader.pages else b""
    except AIContextError:
        raise
    except Exception as exc:
        raise AIContextError(f"could not read embedded career profile: {exc}") from exc

    return profile, shared_context, AIContextManifest(
        mode=mode,
        filename=PROFILE_FILENAME,
        profile_sha256=profile_sha256,
        profile_size=len(profile_bytes),
        shared_context_filename=(SHARED_CONTEXT_FILENAME if shared_context is not None else None),
        shared_context_sha256=shared_context_sha256,
        shared_context_size=shared_context_size,
        actual_text_bridge=b"/ActualText" in page_content,
    )


def read_embedded_profile(pdf: bytes) -> tuple[dict[str, Any], AIContextManifest]:
    profile, _, manifest = read_ai_context_files(pdf)
    return profile, manifest
