"""Trimesh-backed generic mesh conversion for BIMAP."""
from __future__ import annotations
import importlib, tempfile
from pathlib import Path
from typing import Any,BinaryIO
from ...app.ports.model_conversion import ConvertedModelArtifact,ModelConversionCapability,ModelConverter,ModelSourceFormat,ModelSourceInspection,ModelTargetFormat
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from . import _detach_artifact,_materialize_stream,_normalize_output_stem,_zip_generated_files
from logs.logger import PrettyPrinter,get_logger  # type: ignore
logger=get_logger("BIMAP Mesh Model Converter"); printer=PrettyPrinter(); _COMPONENT="mesh_model_converter"
_CAPS={
 ModelSourceFormat.parse("OBJ"):(ModelTargetFormat.GLB,),
 ModelSourceFormat.parse("GLB"):(ModelTargetFormat.OBJ,),
 ModelSourceFormat.parse("STL"):(ModelTargetFormat.GLB,ModelTargetFormat.OBJ),
 ModelSourceFormat.parse("PLY"):(ModelTargetFormat.GLB,ModelTargetFormat.OBJ),
}
class TrimeshModelConverter(ModelConverter):
    __slots__=("_trimesh","_capabilities","_by_format")
    def __init__(self)->None:
        try: self._trimesh=importlib.import_module("trimesh")
        except ImportError as exc:
            raise AppConfigurationError("Trimesh is required for generic mesh conversion.",component=_COMPONENT,operation="initialize",field="dependency",cause=exc) from exc
        self._capabilities=tuple(ModelConversionCapability(source_format=s,extensions=(f".{s.value}",),target_formats=t) for s,t in _CAPS.items()); self._by_format={c.source_format:c for c in self._capabilities}

    @property
    def capabilities(self):
        return self._capabilities

    def _source(self,v,*,operation):
        s=ModelSourceFormat.parse(v); c=self._by_format.get(s)
        if c is None: 
            raise UnsupportedAppInputError("Unsupported generic mesh source format.",component=_COMPONENT,operation=operation,field="source_format")
        return s,c

    def _load(self,path:Path)->Any:
        try:
            loader=getattr(self._trimesh,"load_scene",None)
            scene=loader(str(path),process=False) if callable(loader) else self._trimesh.load(str(path),force="scene",process=False)
        except Exception as exc:
            raise UnsupportedAppInputError("Trimesh could not parse the source model.",component=_COMPONENT,operation="load",field="source",context=lower_error_context(exc),cause=exc) from exc
        if not getattr(scene,"geometry",None):
            raise UnsupportedAppInputError("Mesh source contains no convertible geometry.",component=_COMPONENT,operation="load",field="source")
        return scene

    def inspect(self,stream:BinaryIO,*,source_format:ModelSourceFormat)->ModelSourceInspection:
        s,_=self._source(source_format,operation="inspect")
        with tempfile.TemporaryDirectory(prefix="bimap-mesh-inspect-") as d:
            p=Path(d)/f"source.{s.value}"; _materialize_stream(stream,p,component=_COMPONENT,operation="materialize_source"); scene=self._load(p); count=len(scene.geometry)
        return ModelSourceInspection(source_format=s,schema=s.value.upper(),product_count=count)

    def convert(self,stream:BinaryIO,*,source_format:ModelSourceFormat,target_format:ModelTargetFormat,output_stem:str)->ConvertedModelArtifact:
        s,c=self._source(source_format,operation="convert"); t=ModelTargetFormat.parse(target_format); stem=_normalize_output_stem(output_stem,component=_COMPONENT,operation="convert")
        if t not in c.target_formats:
            raise UnsupportedAppInputError("Requested mesh conversion pair is unsupported.",component=_COMPONENT,operation="convert",field="target_format")
        with tempfile.TemporaryDirectory(prefix="bimap-mesh-convert-") as d:
            d=Path(d); p=d/f"source.{s.value}"; _materialize_stream(stream,p,component=_COMPONENT,operation="materialize_source"); scene=self._load(p)
            try:
                if t is ModelTargetFormat.GLB:
                    out=d/f"{stem}.glb"; payload=scene.export(file_type="glb");
                    if not isinstance(payload,(bytes,bytearray,memoryview)):
                        raise AppIntegrityError("Trimesh GLB export returned non-binary data.",component=_COMPONENT,operation="convert",field="artifact")
                    out.write_bytes(bytes(payload)); 
                    return _detach_artifact(out,filename=out.name,content_type="model/gltf-binary",component=_COMPONENT,operation="detach_artifact")
                obj=d/f"{stem}.obj"; payload=scene.export(file_type="obj"); obj.write_text(payload if isinstance(payload,str) else bytes(payload).decode("utf-8"),encoding="utf-8"); archive=d/f"{stem}-obj.zip"; _zip_generated_files((obj,),archive,component=_COMPONENT,operation="package_obj");
                return _detach_artifact(archive,filename=archive.name,content_type="application/zip",component=_COMPONENT,operation="detach_artifact")
            except AppError: 
                raise
            except Exception as exc: 
                raise AppIntegrityError("Trimesh conversion failed.",component=_COMPONENT,operation="convert",context=lower_error_context(exc),cause=exc) from exc

__all__=["TrimeshModelConverter"]
