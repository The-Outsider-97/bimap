using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Bimap.RevitLocalExporter;

/// <summary>
/// Minimal dependency-free glTF 2.0 binary writer for BIMAP Revit viewer
/// artifacts.
///
/// Supported subset:
/// - triangle primitives;
/// - POSITION and NORMAL attributes;
/// - uint32 indices;
/// - basic PBR materials;
/// - one glTF node per geometry-bearing Revit element;
/// - stable Revit identifiers in node name/extras.
///
/// The implementation deliberately avoids SharpGLTF and other external
/// geometry packages.
/// </summary>
internal static class GlbWriter
{
    private const uint GlbMagic = 0x46546C67;
    private const uint GlbVersion = 2;
    private const uint JsonChunkType = 0x4E4F534A;
    private const uint BinChunkType = 0x004E4942;

    private const int ComponentTypeFloat = 5126;
    private const int ComponentTypeUnsignedInt = 5125;
    private const int BufferTargetArrayBuffer = 34962;
    private const int BufferTargetElementArrayBuffer = 34963;
    private const int PrimitiveModeTriangles = 4;

    private static readonly JsonSerializerOptions JsonOptions =
        new()
        {
            PropertyNamingPolicy =
                JsonNamingPolicy.CamelCase,
            DefaultIgnoreCondition =
                JsonIgnoreCondition.WhenWritingNull,
            WriteIndented = false
        };

    public static GlbWriteResult Write(
        string outputPath,
        RevitExportResult model,
        GlbSourceProvenance? provenance = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(
            outputPath);
        ArgumentNullException.ThrowIfNull(model);

        if (model.Elements.Count == 0)
        {
            throw new InvalidOperationException(
                "Cannot write a GLB without exported Revit elements.");
        }

        string fullPath =
            Path.GetFullPath(outputPath);

        string? directory =
            Path.GetDirectoryName(fullPath);

        if (string.IsNullOrWhiteSpace(directory))
        {
            throw new ArgumentException(
                "The GLB output path must have a parent directory.",
                nameof(outputPath));
        }

        Directory.CreateDirectory(directory);

        string temporaryPath =
            Path.Combine(
                directory,
                $".{Path.GetFileName(fullPath)}.{Guid.NewGuid():N}.tmp");

        try
        {
            BuildGlb(
                temporaryPath,
                model,
                provenance);

            ValidateGlbFile(
                temporaryPath);

            long sizeBytes;
            string sha256;

            using (FileStream stream = new(
                       temporaryPath,
                       FileMode.Open,
                       FileAccess.Read,
                       FileShare.Read,
                       bufferSize: 128 * 1024,
                       FileOptions.SequentialScan))
            {
                sizeBytes = stream.Length;

                sha256 =
                    Convert.ToHexString(
                            SHA256.HashData(stream))
                        .ToLowerInvariant();
            }

            // The hashing stream must be closed before replacement on Windows.
            File.Move(
                temporaryPath,
                fullPath,
                overwrite: true);

            return new GlbWriteResult(
                Path: fullPath,
                SizeBytes: sizeBytes,
                Sha256: sha256);
        }
        finally
        {
            TryDeleteTemporaryFile(
                temporaryPath);
        }
    }

    private static void BuildGlb(
        string path,
        RevitExportResult model,
        GlbSourceProvenance? provenance)
    {
        Dictionary<string, int> materialIndices =
            model.Materials
                .Select(
                    static (material, index) =>
                        new KeyValuePair<string, int>(
                            material.Key,
                            index))
                .ToDictionary(
                    static pair => pair.Key,
                    static pair => pair.Value,
                    StringComparer.Ordinal);

        List<GltfBufferView> bufferViews =
            new();

        List<GltfAccessor> accessors =
            new();

        List<GltfMesh> meshes =
            new();

        List<GltfNode> nodes =
            new();

        using MemoryStream binary =
            new();

        foreach (RevitExportElement element
                 in model.Elements)
        {
            List<GltfPrimitive> gltfPrimitives =
                new();

            foreach (RevitExportPrimitive primitive
                     in element.Primitives)
            {
                if (primitive.Positions.Count == 0 ||
                    primitive.Indices.Count == 0)
                {
                    continue;
                }

                if (primitive.Positions.Count !=
                    primitive.Normals.Count)
                {
                    throw new InvalidOperationException(
                        "A BIMAP GLB primitive has mismatched position/normal counts.");
                }

                if (primitive.Indices.Count % 3 != 0)
                {
                    throw new InvalidOperationException(
                        "A BIMAP GLB triangle primitive has a non-triangular index count.");
                }

                int positionAccessor =
                    AppendVector3Accessor(
                        binary,
                        primitive.Positions,
                        bufferViews,
                        accessors,
                        includeBounds: true);

                int normalAccessor =
                    AppendVector3Accessor(
                        binary,
                        primitive.Normals,
                        bufferViews,
                        accessors,
                        includeBounds: false);

                int indexAccessor =
                    AppendIndexAccessor(
                        binary,
                        primitive.Indices,
                        bufferViews,
                        accessors);

                if (!materialIndices.TryGetValue(
                        primitive.MaterialKey,
                        out int materialIndex))
                {
                    throw new InvalidOperationException(
                        $"No glTF material was registered for key '{primitive.MaterialKey}'.");
                }

                gltfPrimitives.Add(
                    new GltfPrimitive
                    {
                        Attributes =
                            new Dictionary<string, int>(
                                StringComparer.Ordinal)
                            {
                                ["POSITION"] =
                                    positionAccessor,
                                ["NORMAL"] =
                                    normalAccessor
                            },
                        Indices = indexAccessor,
                        Material = materialIndex,
                        Mode = PrimitiveModeTriangles
                    });
            }

            if (gltfPrimitives.Count == 0)
            {
                continue;
            }

            int meshIndex =
                meshes.Count;

            meshes.Add(
                new GltfMesh
                {
                    Name =
                        BuildMeshName(element),
                    Primitives =
                        gltfPrimitives
                });

            nodes.Add(
                new GltfNode
                {
                    /*
                     * BIMModelViewer indexes Object3D.name. Use Revit's
                     * stable UniqueId when available, and also publish both
                     * persistent and numeric IDs in extras.
                     */
                    Name =
                        !string.IsNullOrWhiteSpace(
                            element.UniqueId)
                            ? element.UniqueId
                            : element.ElementId,
                    Mesh = meshIndex,
                    Extras =
                        new Dictionary<string, object?>(
                            StringComparer.Ordinal)
                        {
                            ["elementId"] =
                                element.ElementId,
                            ["element_id"] =
                                element.ElementId,
                            ["revitId"] =
                                element.ElementId,
                            ["revit_id"] =
                                element.ElementId,
                            ["uniqueId"] =
                                element.UniqueId,
                            ["unique_id"] =
                                element.UniqueId,
                            ["category"] =
                                element.Category,
                            ["typeName"] =
                                element.TypeName,
                            ["displayName"] =
                                element.DisplayName
                        }
                });
        }

        if (nodes.Count == 0)
        {
            throw new InvalidOperationException(
                "No glTF nodes were produced from the exported Revit geometry.");
        }

        List<GltfMaterial> materials =
            model.Materials
                .Select(CreateMaterial)
                .ToList();

        GltfRoot root = new()
        {
            Asset =
                new GltfAsset
                {
                    Version = "2.0",
                    Generator =
                        "R3D BIMAP Revit Local Exporter 1.0.0"
                },
            Scene = 0,
            Scenes =
            [
                new GltfScene
                {
                    Nodes =
                        Enumerable
                            .Range(
                                0,
                                nodes.Count)
                            .ToList()
                }
            ],
            Nodes = nodes,
            Meshes = meshes,
            Materials = materials,
            Buffers =
            [
                new GltfBuffer
                {
                    ByteLength =
                        checked((int)binary.Length)
                }
            ],
            BufferViews = bufferViews,
            Accessors = accessors,
            Extras =
                new Dictionary<string, object?>(
                    StringComparer.Ordinal)
                {
                    ["bimapSchema"] =
                        "viewer-model/1.0",
                    ["sourceDocumentTitle"] =
                        model.SourceDocumentTitle,

                    /*
                     * Never publish Document.PathName. BIMAP only needs an
                     * auditable source identity, not the user's local path.
                     */
                    ["sourceFilename"] =
                        provenance?.FileName,
                    ["sourceSha256"] =
                        provenance?.Sha256,
                    ["sourceFingerprintKind"] =
                        provenance?.FingerprintKind,
                    ["sourceDocumentModifiedAtExport"] =
                        provenance?.DocumentModifiedAtExport,

                    ["sourceKind"] =
                        model.SourceKind,
                    ["sourceRevitVersion"] =
                        model.RevitVersion,
                    ["exportedAtUtc"] =
                        model.ExportedAtUtc.ToString(
                            "O",
                            CultureInfo.InvariantCulture),
                    ["units"] =
                        "metres",
                    ["coordinateSystem"] =
                        "right-handed; Y-up; Revit (X,Y,Z) -> glTF (X,Z,-Y)",
                    ["elementCount"] =
                        nodes.Count,
                    ["triangleCount"] =
                        model.TriangleCount,
                    ["vertexCount"] =
                        model.VertexCount
                }
        };

        byte[] json =
            JsonSerializer.SerializeToUtf8Bytes(
                root,
                JsonOptions);

        byte[] paddedJson =
            PadChunk(
                json,
                0x20);

        byte[] binaryBytes =
            binary.ToArray();

        byte[] paddedBinary =
            PadChunk(
                binaryBytes,
                0x00);

        long totalLengthLong =
            12L +
            8L + paddedJson.Length +
            8L + paddedBinary.Length;

        if (totalLengthLong >
            uint.MaxValue)
        {
            throw new InvalidOperationException(
                "The GLB exceeds the maximum 32-bit container length.");
        }

        using FileStream output = new(
            path,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            bufferSize: 128 * 1024,
            FileOptions.SequentialScan);

        using BinaryWriter writer = new(
            output,
            Encoding.UTF8,
            leaveOpen: true);

        writer.Write(GlbMagic);
        writer.Write(GlbVersion);
        writer.Write(
            checked((uint)totalLengthLong));

        writer.Write(
            checked((uint)paddedJson.Length));
        writer.Write(JsonChunkType);
        writer.Write(paddedJson);

        writer.Write(
            checked((uint)paddedBinary.Length));
        writer.Write(BinChunkType);
        writer.Write(paddedBinary);

        writer.Flush();
        output.Flush(flushToDisk: true);
    }

    private static int AppendVector3Accessor(
        MemoryStream binary,
        IReadOnlyList<Vector3> values,
        List<GltfBufferView> bufferViews,
        List<GltfAccessor> accessors,
        bool includeBounds)
    {
        if (values.Count == 0)
        {
            throw new ArgumentException(
                "A VEC3 accessor cannot be empty.",
                nameof(values));
        }

        Align(binary, 4);

        int byteOffset =
            checked((int)binary.Position);

        Vector3 minimum = new(
            float.PositiveInfinity,
            float.PositiveInfinity,
            float.PositiveInfinity);

        Vector3 maximum = new(
            float.NegativeInfinity,
            float.NegativeInfinity,
            float.NegativeInfinity);

        Span<byte> scratch =
            stackalloc byte[12];

        foreach (Vector3 value in values)
        {
            if (!IsFinite(value))
            {
                throw new InvalidOperationException(
                    "The exported Revit mesh contains a non-finite vector.");
            }

            WriteSingle(
                scratch[0..4],
                value.X);

            WriteSingle(
                scratch[4..8],
                value.Y);

            WriteSingle(
                scratch[8..12],
                value.Z);

            binary.Write(scratch);

            if (includeBounds)
            {
                minimum =
                    Vector3.Min(
                        minimum,
                        value);

                maximum =
                    Vector3.Max(
                        maximum,
                        value);
            }
        }

        int byteLength =
            checked(
                (int)binary.Position -
                byteOffset);

        int bufferViewIndex =
            bufferViews.Count;

        bufferViews.Add(
            new GltfBufferView
            {
                Buffer = 0,
                ByteOffset = byteOffset,
                ByteLength = byteLength,
                Target =
                    BufferTargetArrayBuffer
            });

        int accessorIndex =
            accessors.Count;

        accessors.Add(
            new GltfAccessor
            {
                BufferView =
                    bufferViewIndex,
                ByteOffset = 0,
                ComponentType =
                    ComponentTypeFloat,
                Count = values.Count,
                Type = "VEC3",
                Min =
                    includeBounds
                        ? new float[]
                        {
                            minimum.X,
                            minimum.Y,
                            minimum.Z
                        }
                        : null,
                Max =
                    includeBounds
                        ? new float[]
                        {
                            maximum.X,
                            maximum.Y,
                            maximum.Z
                        }
                        : null
            });

        return accessorIndex;
    }

    private static int AppendIndexAccessor(
        MemoryStream binary,
        IReadOnlyList<uint> values,
        List<GltfBufferView> bufferViews,
        List<GltfAccessor> accessors)
    {
        if (values.Count == 0)
        {
            throw new ArgumentException(
                "An index accessor cannot be empty.",
                nameof(values));
        }

        Align(binary, 4);

        int byteOffset =
            checked((int)binary.Position);

        Span<byte> scratch =
            stackalloc byte[4];

        uint maximum = 0;

        foreach (uint value in values)
        {
            BinaryPrimitives
                .WriteUInt32LittleEndian(
                    scratch,
                    value);

            binary.Write(scratch);

            maximum =
                Math.Max(maximum, value);
        }

        int byteLength =
            checked(
                (int)binary.Position -
                byteOffset);

        int bufferViewIndex =
            bufferViews.Count;

        bufferViews.Add(
            new GltfBufferView
            {
                Buffer = 0,
                ByteOffset = byteOffset,
                ByteLength = byteLength,
                Target =
                    BufferTargetElementArrayBuffer
            });

        int accessorIndex =
            accessors.Count;

        accessors.Add(
            new GltfAccessor
            {
                BufferView =
                    bufferViewIndex,
                ByteOffset = 0,
                ComponentType =
                    ComponentTypeUnsignedInt,
                Count = values.Count,
                Type = "SCALAR",
                Min =
                    new uint[] { 0 },
                Max =
                    new uint[] { maximum }
            });

        return accessorIndex;
    }

    private static GltfMaterial CreateMaterial(
        RevitExportMaterial source)
    {
        float alpha =
            Math.Clamp(
                source.BaseColor.W,
                0.0f,
                1.0f);

        return new GltfMaterial
        {
            Name = source.Name,
            PbrMetallicRoughness =
                new GltfPbrMetallicRoughness
                {
                    BaseColorFactor =
                    [
                        source.BaseColor.X,
                        source.BaseColor.Y,
                        source.BaseColor.Z,
                        alpha
                    ],
                    MetallicFactor =
                        0.0f,
                    RoughnessFactor =
                        0.82f
                },
            AlphaMode =
                alpha < 0.999f
                    ? "BLEND"
                    : "OPAQUE",
            DoubleSided = true
        };
    }

    private static string BuildMeshName(
        RevitExportElement element)
    {
        string name =
            string.IsNullOrWhiteSpace(
                element.DisplayName)
                ? "RevitElement"
                : element.DisplayName;

        return
            $"{name} [{element.ElementId}]";
    }

    private static void Align(
        MemoryStream stream,
        int alignment)
    {
        if (alignment <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(alignment));
        }

        while (stream.Position % alignment != 0)
        {
            stream.WriteByte(0);
        }
    }

    private static byte[] PadChunk(
        byte[] source,
        byte padding)
    {
        int paddedLength =
            checked(
                (source.Length + 3) &
                ~3);

        if (paddedLength ==
            source.Length)
        {
            return source;
        }

        byte[] result =
            new byte[paddedLength];

        Buffer.BlockCopy(
            source,
            0,
            result,
            0,
            source.Length);

        for (int index = source.Length;
             index < result.Length;
             index++)
        {
            result[index] =
                padding;
        }

        return result;
    }

    private static void WriteSingle(
        Span<byte> destination,
        float value)
    {
        BinaryPrimitives
            .WriteInt32LittleEndian(
                destination,
                BitConverter.SingleToInt32Bits(
                    value));
    }

    private static bool IsFinite(
        Vector3 value)
    {
        return
            float.IsFinite(value.X) &&
            float.IsFinite(value.Y) &&
            float.IsFinite(value.Z);
    }

    private static void ValidateGlbFile(
        string path)
    {
        FileInfo info =
            new(path);

        if (!info.Exists ||
            info.Length < 20)
        {
            throw new InvalidDataException(
                "The generated GLB is missing or too small.");
        }

        Span<byte> header =
            stackalloc byte[12];

        using FileStream stream = new(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read);

        int read =
            stream.Read(header);

        if (read != header.Length)
        {
            throw new InvalidDataException(
                "The generated GLB header is incomplete.");
        }

        uint magic =
            BinaryPrimitives
                .ReadUInt32LittleEndian(
                    header[0..4]);

        uint version =
            BinaryPrimitives
                .ReadUInt32LittleEndian(
                    header[4..8]);

        uint declaredLength =
            BinaryPrimitives
                .ReadUInt32LittleEndian(
                    header[8..12]);

        if (magic != GlbMagic)
        {
            throw new InvalidDataException(
                "The generated file is not a GLB container.");
        }

        if (version != GlbVersion)
        {
            throw new InvalidDataException(
                $"Unsupported GLB version '{version}'.");
        }

        if ((long)declaredLength !=
            info.Length)
        {
            throw new InvalidDataException(
                "The GLB declared length does not match the physical file length.");
        }
    }

    private static void TryDeleteTemporaryFile(
        string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (IOException)
        {
            // Best-effort cleanup; never hide the primary export result/error.
        }
        catch (UnauthorizedAccessException)
        {
            // Best-effort cleanup; never hide the primary export result/error.
        }
    }

    private sealed class GltfRoot
    {
        public required GltfAsset Asset { get; init; }

        public required int Scene { get; init; }

        public required List<GltfScene> Scenes { get; init; }

        public required List<GltfNode> Nodes { get; init; }

        public required List<GltfMesh> Meshes { get; init; }

        public required List<GltfMaterial> Materials { get; init; }

        public required List<GltfBuffer> Buffers { get; init; }

        public required List<GltfBufferView> BufferViews { get; init; }

        public required List<GltfAccessor> Accessors { get; init; }

        public Dictionary<string, object?>? Extras { get; init; }
    }

    private sealed class GltfAsset
    {
        public required string Version { get; init; }

        public required string Generator { get; init; }
    }

    private sealed class GltfScene
    {
        public required List<int> Nodes { get; init; }
    }

    private sealed class GltfNode
    {
        public required string Name { get; init; }

        public required int Mesh { get; init; }

        public Dictionary<string, object?>? Extras { get; init; }
    }

    private sealed class GltfMesh
    {
        public required string Name { get; init; }

        public required List<GltfPrimitive> Primitives { get; init; }
    }

    private sealed class GltfPrimitive
    {
        public required Dictionary<string, int>
            Attributes { get; init; }

        public required int Indices { get; init; }

        public required int Material { get; init; }

        public required int Mode { get; init; }
    }

    private sealed class GltfMaterial
    {
        public required string Name { get; init; }

        public required GltfPbrMetallicRoughness
            PbrMetallicRoughness { get; init; }

        public required string AlphaMode { get; init; }

        public required bool DoubleSided { get; init; }
    }

    private sealed class GltfPbrMetallicRoughness
    {
        public required float[]
            BaseColorFactor { get; init; }

        public required float
            MetallicFactor { get; init; }

        public required float
            RoughnessFactor { get; init; }
    }

    private sealed class GltfBuffer
    {
        public required int ByteLength { get; init; }
    }

    private sealed class GltfBufferView
    {
        public required int Buffer { get; init; }

        public required int ByteOffset { get; init; }

        public required int ByteLength { get; init; }

        public required int Target { get; init; }
    }

    private sealed class GltfAccessor
    {
        public required int BufferView { get; init; }

        public required int ByteOffset { get; init; }

        public required int ComponentType { get; init; }

        public required int Count { get; init; }

        public required string Type { get; init; }

        public Array? Min { get; init; }

        public Array? Max { get; init; }
    }
}

internal sealed record GlbWriteResult(
    string Path,
    long SizeBytes,
    string Sha256);

/// <summary>
/// Source identity embedded in the GLB root extras.
///
/// Sha256 is the digest of the saved local RFA/RVT file. It is deliberately
/// absent when BIMAP cannot safely fingerprint a saved local source.
/// </summary>
internal sealed record GlbSourceProvenance(
    string FileName,
    string Sha256,
    string FingerprintKind,
    bool DocumentModifiedAtExport);
