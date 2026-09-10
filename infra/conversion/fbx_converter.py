"""Blender-backed FBX model converter for BIMAP."""
from __future__ import annotations

import json, shutil, subprocess, tempfile

from pathlib import Path
from typing import BinaryIO

from ...app.ports.model_conversion import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from . import _detach_artifact, _materialize_stream, _normalize_output_stem, _zip_generated_files
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger=get_logger("BIMAP FBX Model Converter")
printer=PrettyPrinter()


_COMPONENT="fbx_model_converter"

class BlenderFbxModelConverter(ModelConverter):

    __slots__=("_blender","_timeout_seconds")

    _CAPABILITY=ModelConversionCapability(source_format=ModelSourceFormat.parse("FBX"),extensions=(".fbx",),target_formats=(ModelTargetFormat.GLB,ModelTargetFormat.OBJ))

    def __init__(self, blender_executable:str="blender", *, timeout_seconds:int=300)->None:
        executable=require_app_text(blender_executable,field="blender_executable",error_type=AppConfigurationError,component=_COMPONENT,operation="initialize",max_length=4096)
        resolved=shutil.which(executable)
        if resolved is None:
            path=Path(executable).expanduser()
            if path.is_file(): resolved=str(path.resolve())
        if resolved is None:
            raise AppConfigurationError("Blender executable could not be resolved.",component=_COMPONENT,operation="initialize",field="blender_executable")
        if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,int) or timeout_seconds<=0:
            raise AppConfigurationError("timeout_seconds must be a positive integer.",component=_COMPONENT,operation="initialize",field="timeout_seconds")
        self._blender=resolved; self._timeout_seconds=timeout_seconds

    @property
    def capabilities(self): return (self._CAPABILITY,)

    @staticmethod
    def _require_source(value,*,operation):
        source=ModelSourceFormat.parse(value)
        if source is not ModelSourceFormat.parse("FBX"):
            raise UnsupportedAppInputError("FBX converter accepts FBX sources only.",component=_COMPONENT,operation=operation,field="source_format")
        return source

    def _run(self, script:Path, *args:str)->subprocess.CompletedProcess[str]:
        try:
            return subprocess.run([self._blender,"--background","--factory-startup","--python",str(script),"--",*args],check=False,capture_output=True,text=True,timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise AppIntegrityError("Blender conversion timed out.",component=_COMPONENT,operation="run_blender",cause=exc) from exc
        except OSError as exc:
            raise AppConfigurationError("Blender could not be started.",component=_COMPONENT,operation="run_blender",field="blender_executable",cause=exc) from exc

    @staticmethod
    def _script(path:Path)->None:
        path.write_text(
            """import bpy,sys
            \nargs=sys.argv[sys.argv.index('--')+1:]
            \nsource,target,out=args
            \nbpy.ops.wm.read_factory_settings(use_empty=True)
            \nr=bpy.ops.import_scene.fbx(filepath=source)
            \nif 'FINISHED' not in r: raise RuntimeError('FBX import failed')
            \ncount=sum(1 for o in bpy.context.scene.objects if o.type=='MESH')
            \nprint('__BIMAP_MESH_COUNT__='+str(count))
            \nif target=='glb':
            \n r=bpy.ops.export_scene.gltf(filepath=out,export_format='GLB')
            \nelse:
            \n if bpy.app.version >= (4,0,0): r=bpy.ops.wm.obj_export(filepath=out,export_materials=True)
            \n else: r=bpy.ops.export_scene.obj(filepath=out,use_materials=True)
            \nif 'FINISHED' not in r: raise RuntimeError('Export failed')
            \n""",encoding="utf-8"
            )

    def inspect(self,stream:BinaryIO,*,source_format:ModelSourceFormat)->ModelSourceInspection:
        self._require_source(source_format,operation="inspect")
        with tempfile.TemporaryDirectory(prefix="bimap-fbx-inspect-") as d:
            d=Path(d); src=d/"source.fbx"; script=d/"fbx.py"; out=d/"probe.glb"
            _materialize_stream(stream,src,component=_COMPONENT,operation="materialize_source"); self._script(script)
            cp=self._run(script,str(src),"glb",str(out))
            if cp.returncode!=0:
                raise UnsupportedAppInputError("Blender could not read the FBX source.",component=_COMPONENT,operation="inspect",field="source",context={"returncode":cp.returncode})
            count=0
            for line in cp.stdout.splitlines():
                if line.startswith("__BIMAP_MESH_COUNT__="):
                    try: count=int(line.split("=",1)[1])
                    except ValueError: count=0
            if count<=0: raise UnsupportedAppInputError("FBX source contains no mesh geometry.",component=_COMPONENT,operation="inspect",field="source")
            return ModelSourceInspection(source_format=ModelSourceFormat.parse("FBX"),schema="FBX",product_count=count)

    def convert(self,stream:BinaryIO,*,source_format:ModelSourceFormat,target_format:ModelTargetFormat,output_stem:str)->ConvertedModelArtifact:
        self._require_source(source_format,operation="convert"); target=ModelTargetFormat.parse(target_format); stem=_normalize_output_stem(output_stem,component=_COMPONENT,operation="convert")
        if target not in self._CAPABILITY.target_formats:
            raise UnsupportedAppInputError("FBX target format is unsupported.",component=_COMPONENT,operation="convert",field="target_format")
        with tempfile.TemporaryDirectory(prefix="bimap-fbx-convert-") as d:
            d=Path(d); src=d/"source.fbx"; script=d/"fbx.py"; primary=d/f"{stem}.{target.value}"
            _materialize_stream(stream,src,component=_COMPONENT,operation="materialize_source"); self._script(script); cp=self._run(script,str(src),target.value,str(primary))
            if cp.returncode!=0:
                raise AppIntegrityError("Blender FBX conversion failed.",component=_COMPONENT,operation="convert",context={"returncode":cp.returncode})
            if target is ModelTargetFormat.GLB:
                return _detach_artifact(primary,filename=primary.name,content_type="model/gltf-binary",component=_COMPONENT,operation="detach_artifact")
            archive=d/f"{stem}-obj.zip"; generated=tuple(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in {".obj",".mtl",".png",".jpg",".jpeg",".tga",".bmp"}); _zip_generated_files(generated,archive,component=_COMPONENT,operation="package_obj")
            return _detach_artifact(archive,filename=archive.name,content_type="application/zip",component=_COMPONENT,operation="detach_artifact")

__all__=["BlenderFbxModelConverter"]
