"""Bounded extraction of identity from compiled APK/AAB manifests."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, cast

from apkInspector.axml import get_manifest  # type: ignore[import-untyped]
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError, Message

from storehelper.artifacts.identity import ArtifactIdentity
from storehelper.stores.huawei.package import PackageError

_ANDROID_NS = "http://schemas.android.com/apk/res/android"
_MAX_MANIFEST_SIZE = 8 * 1024 * 1024
_VERSION_CODE = re.compile(r"[0-9]+")
_IDENTITY_FIELDS = {"package", "versionCode", "versionName"}


def _invalid(reason: str) -> PackageError:
    return PackageError("PACKAGE_METADATA_INVALID", f"Android manifest {reason}.")


def _add_field(
    message: descriptor_pb2.DescriptorProto,
    name: str,
    number: int,
    field_type: int,
    *,
    type_name: str = "",
    repeated: bool = False,
) -> None:
    field = message.field.add()
    field.name = name
    field.number = number
    field.type = cast(Any, field_type)
    field.label = cast(Any, 3 if repeated else 1)
    if type_name:
        field.type_name = f".aapt.pb.{type_name}"


def _xml_node_type() -> type[Message]:
    """The relevant wire fields of AOSP tools/aapt2/Resources.proto."""
    file = descriptor_pb2.FileDescriptorProto()
    file.name = "storehelper_aapt_xml.proto"
    file.package = "aapt.pb"
    file.syntax = "proto3"
    string = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    message = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    int32 = descriptor_pb2.FieldDescriptorProto.TYPE_INT32
    uint32 = descriptor_pb2.FieldDescriptorProto.TYPE_UINT32

    node = file.message_type.add()
    node.name = "XmlNode"
    _add_field(node, "element", 1, message, type_name="XmlElement")
    _add_field(node, "text", 2, string)

    element = file.message_type.add()
    element.name = "XmlElement"
    _add_field(element, "namespace_uri", 2, string)
    _add_field(element, "name", 3, string)
    _add_field(element, "attribute", 4, message, type_name="XmlAttribute", repeated=True)

    attribute = file.message_type.add()
    attribute.name = "XmlAttribute"
    _add_field(attribute, "namespace_uri", 1, string)
    _add_field(attribute, "name", 2, string)
    _add_field(attribute, "value", 3, string)
    _add_field(attribute, "compiled_item", 6, message, type_name="Item")

    item = file.message_type.add()
    item.name = "Item"
    _add_field(item, "ref", 1, message, type_name="Reference")
    _add_field(item, "str", 2, message, type_name="String")
    _add_field(item, "prim", 7, message, type_name="Primitive")

    reference = file.message_type.add()
    reference.name = "Reference"
    _add_field(reference, "id", 2, uint32)
    _add_field(reference, "name", 3, string)

    value_string = file.message_type.add()
    value_string.name = "String"
    _add_field(value_string, "value", 1, string)

    primitive = file.message_type.add()
    primitive.name = "Primitive"
    _add_field(primitive, "int_decimal_value", 6, int32)
    _add_field(primitive, "int_hexadecimal_value", 7, uint32)

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("aapt.pb.XmlNode"))


_XmlNode = _xml_node_type()


def _read_manifest(path: Path) -> bytes:
    if path.suffix.lower() == ".apk":
        member_name = "AndroidManifest.xml"
    elif path.suffix.lower() == ".aab":
        member_name = "base/manifest/AndroidManifest.xml"
    else:
        raise _invalid("requires an APK or AAB file")
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if info.filename == member_name]
            if len(members) != 1:
                raise _invalid("must contain exactly one expected manifest member")
            member = members[0]
            if member.file_size > _MAX_MANIFEST_SIZE:
                raise _invalid("exceeds the 8 MiB metadata limit")
            with archive.open(member) as source:
                data = source.read(_MAX_MANIFEST_SIZE + 1)
            if len(data) > _MAX_MANIFEST_SIZE:
                raise _invalid("exceeds the 8 MiB metadata limit")
            return data
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError) as error:
        raise _invalid("cannot be read from the package") from error


def _identity(package: str | None, code: str | None, name: str | None) -> ArtifactIdentity:
    if package is None:
        raise _invalid("is missing the root package attribute")
    if code is None:
        raise _invalid("is missing android:versionCode")
    if not _VERSION_CODE.fullmatch(code):
        raise _invalid("has an invalid android:versionCode")
    if name is not None and (not name or name.startswith(("@", "?"))):
        raise _invalid("has an unresolved android:versionName resource reference")
    try:
        return ArtifactIdentity(package_name=package, version_code=int(code), version_name=name)
    except ValueError as error:
        raise _invalid("has an invalid package or version value") from error


def _apk_identity(data: bytes) -> ArtifactIdentity | None:
    # Legacy tests use opaque placeholder bytes; a binary-XML header is unambiguous.
    if not data.startswith(b"\x03\x00"):
        return None
    try:
        decoded = get_manifest(io.BytesIO(data))
        root = ET.fromstring(decoded)
    except Exception as error:  # Third-party parser handles hostile input.
        raise _invalid("contains malformed compiled XML") from error
    if root.tag != "manifest":
        raise _invalid("root must be <manifest>")
    for key in root.attrib:
        namespace, _, local_name = key.rpartition("}")
        namespace = namespace.removeprefix("{") if namespace else ""
        if local_name in _IDENTITY_FIELDS and namespace != (
            "" if local_name == "package" else _ANDROID_NS
        ):
            raise _invalid("has an identity attribute in the wrong namespace")
    return _identity(
        root.attrib.get("package"),
        root.attrib.get(f"{{{_ANDROID_NS}}}versionCode"),
        root.attrib.get(f"{{{_ANDROID_NS}}}versionName"),
    )


def _compiled_value(attribute: Any) -> str | None:
    if not attribute.HasField("compiled_item"):
        return None
    item = attribute.compiled_item
    if item.HasField("ref"):
        raise _invalid("has an unresolved resource reference")
    if item.HasField("str"):
        return str(item.str.value)
    if item.HasField("prim"):
        fields = item.prim.ListFields()
        for field, value in fields:
            if field.name in {"int_decimal_value", "int_hexadecimal_value"}:
                return str(value)
    return None


def _aab_identity(data: bytes) -> ArtifactIdentity | None:
    if not data or data[0] not in (0x0A, 0x12, 0x1A):
        return None
    try:
        node = cast(Any, _XmlNode())
        node.ParseFromString(data)
    except DecodeError as error:
        raise _invalid("contains malformed protobuf XML") from error
    if (
        not node.HasField("element")
        or node.element.name != "manifest"
        or node.element.namespace_uri
    ):
        raise _invalid("protobuf XML root must be <manifest>")
    attributes: dict[tuple[str, str], str] = {}
    for attribute in node.element.attribute:
        namespace, local_name = attribute.namespace_uri, attribute.name
        key = (namespace, local_name)
        if key in attributes:
            raise _invalid("has a duplicate root attribute")
        if local_name in _IDENTITY_FIELDS and namespace != (
            "" if local_name == "package" else _ANDROID_NS
        ):
            raise _invalid("has an identity attribute in the wrong namespace")
        compiled = _compiled_value(attribute) if local_name in _IDENTITY_FIELDS else None
        raw = attribute.value
        if compiled is not None and raw and raw != compiled:
            raise _invalid("has conflicting root attribute values")
        attributes[key] = raw or compiled or ""
    return _identity(
        attributes.get(("", "package")),
        attributes.get((_ANDROID_NS, "versionCode")),
        attributes.get((_ANDROID_NS, "versionName")),
    )


def inspect_android_metadata(path: Path) -> ArtifactIdentity | None:
    """Read only the bounded manifest member; return None for opaque legacy placeholders."""
    data = _read_manifest(path)
    return _apk_identity(data) if path.suffix.lower() == ".apk" else _aab_identity(data)
