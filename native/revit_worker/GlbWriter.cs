using System.Text;
using System.Text.Json;

namespace Bimap.RevitWorker.AppBundle;

public static class GlbWriter
{
    private const uint GlbMagic = 0x46546C67;
    private const uint GlbVersion = 2;
    private const uint JsonChunkType = 0x4E4F534A;
    private const uint BinChunkType = 0x004E4942;
    private const int FloatComponentType = 5126;
    private const int UnsignedIntComponentType = 5125;
    private const int ArrayBufferTarget = 34962;
    private const int ElementArrayBufferTarget = 34963;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = false,
        PropertyNamingPolicy = null,
    };

    public static void Write(string destinationPath, ExportModel model, string generatorLabel)
    {
        if (string.IsNullOrWhiteSpace(destinationPath))
        {
            throw new ArgumentException("A GLB destination is required.", nameof(destinationPath));
        }
        ArgumentNullException.ThrowIfNull(model);

        var binary = new MemoryStream();
        var bufferViews = new List<Dictionary<string, object?>>();
        var accessors = new List<Dictionary<string, object?>>();
        var meshes = new List<Dictionary<string, object?>>();
        var nodes = new List<Dictionary<string, object?>>();

        var materialRows = model.Materials.Values
            .OrderBy(material => material.Id)
            .ToArray();
        var materialIndex = materialRows
            .Select((material, index) => (material.Id, index))
            .ToDictionary(pair => pair.Id, pair => pair.index);

        foreach (var element in model.Elements)
        {
            var gltfPrimitives = new List<Dictionary<string, object?>>();
            foreach (var primitive in element.Primitives)
            {
                if (primitive.Indices.Count == 0 || primitive.Positions.Count == 0)
                {
                    continue;
                }
                if (primitive.Positions.Count != primitive.Normals.Count)
                {
                    throw new InvalidOperationException("GLB primitive position/normal counts are inconsistent.");
                }

                var positionAccessor = AppendVectorAccessor(
                    binary,
                    bufferViews,
                    accessors,
                    primitive.Positions,
                    includeBounds: true);
                var normalAccessor = AppendVectorAccessor(
                    binary,
                    bufferViews,
                    accessors,
                    primitive.Normals,
                    includeBounds: false);
                var indexAccessor = AppendIndexAccessor(
                    binary,
                    bufferViews,
                    accessors,
                    primitive.Indices);

                gltfPrimitives.Add(new Dictionary<string, object?>
                {
                    ["attributes"] = new Dictionary<string, object?>
                    {
                        ["POSITION"] = positionAccessor,
                        ["NORMAL"] = normalAccessor,
                    },
                    ["indices"] = indexAccessor,
                    ["material"] = materialIndex.TryGetValue(primitive.MaterialId, out var index)
                        ? index
                        : materialIndex[-1],
                    ["mode"] = 4,
                });
            }

            if (gltfPrimitives.Count == 0)
            {
                continue;
            }

            var meshIndex = meshes.Count;
            meshes.Add(new Dictionary<string, object?>
            {
                ["name"] = element.UniqueId,
                ["primitives"] = gltfPrimitives,
            });

            nodes.Add(new Dictionary<string, object?>
            {
                ["name"] = element.UniqueId,
                ["mesh"] = meshIndex,
                ["extras"] = new Dictionary<string, object?>
                {
                    ["elementId"] = element.ElementId,
                    ["revitId"] = element.ElementId,
                    ["uniqueId"] = element.UniqueId,
                    ["category"] = element.Category,
                    ["typeName"] = element.TypeName,
                    ["displayName"] = element.Name,
                },
            });
        }

        if (nodes.Count == 0)
        {
            throw new InvalidOperationException("The Revit document contained no triangulated model geometry to write to GLB.");
        }

        var materials = materialRows
            .Select(material => new Dictionary<string, object?>
            {
                ["name"] = material.Name,
                ["pbrMetallicRoughness"] = new Dictionary<string, object?>
                {
                    ["baseColorFactor"] = new[]
                    {
                        Clamp01(material.Red),
                        Clamp01(material.Green),
                        Clamp01(material.Blue),
                        Clamp01(material.Alpha),
                    },
                    ["metallicFactor"] = 0.0,
                    ["roughnessFactor"] = 0.8,
                },
                ["alphaMode"] = material.Alpha < 0.999 ? "BLEND" : "OPAQUE",
                ["doubleSided"] = true,
                ["extras"] = new Dictionary<string, object?>
                {
                    ["revitMaterialId"] = material.Id.ToString(System.Globalization.CultureInfo.InvariantCulture),
                },
            })
            .ToArray();

        var sceneNodeIndices = Enumerable.Range(0, nodes.Count).ToArray();
        var gltf = new Dictionary<string, object?>
        {
            ["asset"] = new Dictionary<string, object?>
            {
                ["version"] = "2.0",
                ["generator"] = string.IsNullOrWhiteSpace(generatorLabel)
                    ? "BIMAP Native Revit Worker"
                    : $"BIMAP Native Revit Worker / {generatorLabel.Trim()}",
            },
            ["scene"] = 0,
            ["scenes"] = new object[]
            {
                new Dictionary<string, object?>
                {
                    ["name"] = "BIMAP Revit model",
                    ["nodes"] = sceneNodeIndices,
                },
            },
            ["nodes"] = nodes,
            ["meshes"] = meshes,
            ["materials"] = materials,
            ["buffers"] = new object[]
            {
                new Dictionary<string, object?>
                {
                    ["byteLength"] = checked((int)binary.Length),
                },
            },
            ["bufferViews"] = bufferViews,
            ["accessors"] = accessors,
            ["extras"] = new Dictionary<string, object?>
            {
                ["bimapGeometrySummary"] = new Dictionary<string, object?>
                {
                    ["totalPolygons"] = model.Summary.TotalPolygons,
                    ["totalVertices"] = model.Summary.TotalVertices,
                    ["totalUniqueEdges"] = model.Summary.TotalUniqueEdges,
                    ["volume"] = model.Summary.Volume,
                    ["volumeUnit"] = "m3",
                    ["watertightGeometries"] = model.Summary.WatertightGeometries,
                },
            },
        };

        var jsonBytes = JsonSerializer.SerializeToUtf8Bytes(gltf, JsonOptions);
        var jsonChunk = Pad(jsonBytes, 0x20);
        var binaryChunk = Pad(binary.ToArray(), 0x00);
        var totalLength = checked(12 + 8 + jsonChunk.Length + 8 + binaryChunk.Length);

        var temporary = destinationPath + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
            using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true))
            {
                writer.Write(GlbMagic);
                writer.Write(GlbVersion);
                writer.Write(checked((uint)totalLength));

                writer.Write(checked((uint)jsonChunk.Length));
                writer.Write(JsonChunkType);
                writer.Write(jsonChunk);

                writer.Write(checked((uint)binaryChunk.Length));
                writer.Write(BinChunkType);
                writer.Write(binaryChunk);
                writer.Flush();
                stream.Flush(flushToDisk: true);
            }

            if (File.Exists(destinationPath))
            {
                File.Delete(destinationPath);
            }
            File.Move(temporary, destinationPath);
        }
        finally
        {
            if (File.Exists(temporary))
            {
                File.Delete(temporary);
            }
        }
    }

    private static int AppendVectorAccessor(
        MemoryStream binary,
        IList<Dictionary<string, object?>> bufferViews,
        IList<Dictionary<string, object?>> accessors,
        IReadOnlyList<Vector3f> values,
        bool includeBounds)
    {
        Align(binary, 4);
        var byteOffset = checked((int)binary.Position);
        using (var writer = new BinaryWriter(binary, Encoding.UTF8, leaveOpen: true))
        {
            foreach (var value in values)
            {
                writer.Write(value.X);
                writer.Write(value.Y);
                writer.Write(value.Z);
            }
        }
        var byteLength = checked((int)binary.Position - byteOffset);
        var bufferViewIndex = bufferViews.Count;
        bufferViews.Add(new Dictionary<string, object?>
        {
            ["buffer"] = 0,
            ["byteOffset"] = byteOffset,
            ["byteLength"] = byteLength,
            ["byteStride"] = 12,
            ["target"] = ArrayBufferTarget,
        });

        var accessor = new Dictionary<string, object?>
        {
            ["bufferView"] = bufferViewIndex,
            ["byteOffset"] = 0,
            ["componentType"] = FloatComponentType,
            ["count"] = values.Count,
            ["type"] = "VEC3",
        };

        if (includeBounds)
        {
            var minX = values.Min(value => value.X);
            var minY = values.Min(value => value.Y);
            var minZ = values.Min(value => value.Z);
            var maxX = values.Max(value => value.X);
            var maxY = values.Max(value => value.Y);
            var maxZ = values.Max(value => value.Z);
            accessor["min"] = new[] { minX, minY, minZ };
            accessor["max"] = new[] { maxX, maxY, maxZ };
        }

        var accessorIndex = accessors.Count;
        accessors.Add(accessor);
        return accessorIndex;
    }

    private static int AppendIndexAccessor(
        MemoryStream binary,
        IList<Dictionary<string, object?>> bufferViews,
        IList<Dictionary<string, object?>> accessors,
        IReadOnlyList<uint> values)
    {
        Align(binary, 4);
        var byteOffset = checked((int)binary.Position);
        using (var writer = new BinaryWriter(binary, Encoding.UTF8, leaveOpen: true))
        {
            foreach (var value in values)
            {
                writer.Write(value);
            }
        }
        var byteLength = checked((int)binary.Position - byteOffset);
        var bufferViewIndex = bufferViews.Count;
        bufferViews.Add(new Dictionary<string, object?>
        {
            ["buffer"] = 0,
            ["byteOffset"] = byteOffset,
            ["byteLength"] = byteLength,
            ["target"] = ElementArrayBufferTarget,
        });

        var accessorIndex = accessors.Count;
        accessors.Add(new Dictionary<string, object?>
        {
            ["bufferView"] = bufferViewIndex,
            ["byteOffset"] = 0,
            ["componentType"] = UnsignedIntComponentType,
            ["count"] = values.Count,
            ["type"] = "SCALAR",
            ["min"] = new[] { values.Min() },
            ["max"] = new[] { values.Max() },
        });
        return accessorIndex;
    }

    private static void Align(MemoryStream stream, int alignment)
    {
        while (stream.Position % alignment != 0)
        {
            stream.WriteByte(0);
        }
    }

    private static byte[] Pad(byte[] source, byte padding)
    {
        var paddedLength = (source.Length + 3) & ~3;
        if (paddedLength == source.Length)
        {
            return source;
        }
        var result = new byte[paddedLength];
        Buffer.BlockCopy(source, 0, result, 0, source.Length);
        for (var index = source.Length; index < result.Length; index++)
        {
            result[index] = padding;
        }
        return result;
    }

    private static double Clamp01(double value) => Math.Clamp(value, 0.0, 1.0);
}
