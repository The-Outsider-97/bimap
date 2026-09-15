using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Numerics;
using Autodesk.Revit.DB;

namespace Bimap.RevitLocalExporter;

/// <summary>
/// Extracts renderable Revit geometry into a provider-neutral in-memory mesh
/// representation consumed only by <see cref="GlbWriter"/>.
///
/// No BIMAP application/domain assembly is referenced here. This keeps the
/// native Revit add-in isolated from the Python application and avoids circular
/// dependencies.
/// </summary>
internal static class RevitGeometryExporter
{
    private const float FeetToMetres = 0.3048f;
    private const double DegenerateNormalEpsilon = 1.0e-20;

    public static RevitExportResult Export(Document document)
    {
        ArgumentNullException.ThrowIfNull(document);

        Options options = new()
        {
            ComputeReferences = false,
            IncludeNonVisibleObjects = false,
            DetailLevel = ViewDetailLevel.Fine
        };

        MaterialRegistry materials =
            new(document);

        List<RevitExportElement> exportedElements =
            new();

        IReadOnlyList<Element> elements =
            new FilteredElementCollector(document)
                .WhereElementIsNotElementType()
                .ToElements()
                .Where(IsCandidateElement)
                .OrderBy(
                    static element => element.Id.Value)
                .ToArray();

        long triangleCount = 0;
        long vertexCount = 0;

        foreach (Element element in elements)
        {
            GeometryElement? geometry;

            try
            {
                geometry =
                    element.get_Geometry(options);
            }
            catch (Autodesk.Revit.Exceptions.InvalidOperationException)
            {
                continue;
            }
            catch (Autodesk.Revit.Exceptions.ArgumentException)
            {
                continue;
            }

            if (geometry is null)
            {
                continue;
            }

            ElementMeshBuilder builder = new(
                document,
                element,
                materials);

            ProcessGeometryElement(
                geometry,
                Transform.Identity,
                builder);

            RevitExportElement? exported =
                builder.Build();

            if (exported is null)
            {
                continue;
            }

            exportedElements.Add(exported);
            triangleCount += exported.TriangleCount;
            vertexCount += exported.VertexCount;
        }

        return new RevitExportResult(
            SourceDocumentTitle:
                document.Title ?? string.Empty,
            SourceDocumentPath:
                document.PathName ?? string.Empty,
            SourceKind:
                document.IsFamilyDocument ? "rfa" : "rvt",
            RevitVersion:
                document.Application.VersionNumber ??
                string.Empty,
            ExportedAtUtc:
                DateTimeOffset.UtcNow,
            Elements:
                new ReadOnlyCollection<RevitExportElement>(
                    exportedElements),
            Materials:
                materials.Snapshot(),
            TriangleCount:
                triangleCount,
            VertexCount:
                vertexCount);
    }

    private static bool IsCandidateElement(
        Element element)
    {
        if (element is null)
        {
            return false;
        }

        if (element.ViewSpecific)
        {
            return false;
        }

        return element switch
        {
            View => false,
            Sketch => false,
            Level => false,
            _ => true
        };
    }

    private static void ProcessGeometryElement(
        GeometryElement geometry,
        Transform transform,
        ElementMeshBuilder builder)
    {
        foreach (GeometryObject geometryObject in geometry)
        {
            switch (geometryObject)
            {
                case Solid solid:
                    ProcessSolid(
                        solid,
                        transform,
                        builder);
                    break;

                case Mesh mesh:
                    ProcessMesh(
                        mesh,
                        transform,
                        builder,
                        ElementId.InvalidElementId);
                    break;

                case GeometryInstance instance:
                    ProcessGeometryInstance(
                        instance,
                        transform,
                        builder);
                    break;
            }
        }
    }

    private static void ProcessGeometryInstance(
        GeometryInstance instance,
        Transform parentTransform,
        ElementMeshBuilder builder)
    {
        Transform instanceTransform =
            parentTransform.Multiply(
                instance.Transform);

        GeometryElement? symbolGeometry;

        try
        {
            symbolGeometry =
                instance.GetSymbolGeometry();
        }
        catch (Autodesk.Revit.Exceptions.InvalidOperationException)
        {
            return;
        }

        if (symbolGeometry is null)
        {
            return;
        }

        ProcessGeometryElement(
            symbolGeometry,
            instanceTransform,
            builder);
    }

    private static void ProcessSolid(
        Solid solid,
        Transform transform,
        ElementMeshBuilder builder)
    {
        if (solid.Faces.Size == 0)
        {
            return;
        }

        foreach (Face face in solid.Faces)
        {
            Mesh? mesh;

            try
            {
                mesh = face.Triangulate();
            }
            catch (Autodesk.Revit.Exceptions.InvalidOperationException)
            {
                continue;
            }

            if (mesh is null ||
                mesh.NumTriangles <= 0)
            {
                continue;
            }

            ProcessMesh(
                mesh,
                transform,
                builder,
                face.MaterialElementId);
        }
    }

    private static void ProcessMesh(
        Mesh mesh,
        Transform transform,
        ElementMeshBuilder builder,
        ElementId materialId)
    {
        int triangleCount =
            mesh.NumTriangles;

        for (int triangleIndex = 0;
             triangleIndex < triangleCount;
             triangleIndex++)
        {
            MeshTriangle triangle =
                mesh.get_Triangle(triangleIndex);

            XYZ p0 = transform.OfPoint(
                triangle.get_Vertex(0));

            XYZ p1 = transform.OfPoint(
                triangle.get_Vertex(1));

            XYZ p2 = transform.OfPoint(
                triangle.get_Vertex(2));

            Vector3 v0 =
                ToGltfPosition(p0);

            Vector3 v1 =
                ToGltfPosition(p1);

            Vector3 v2 =
                ToGltfPosition(p2);

            Vector3 cross =
                Vector3.Cross(
                    v1 - v0,
                    v2 - v0);

            double lengthSquared =
                (double)cross.X * cross.X +
                (double)cross.Y * cross.Y +
                (double)cross.Z * cross.Z;

            if (!double.IsFinite(lengthSquared) ||
                lengthSquared <=
                    DegenerateNormalEpsilon)
            {
                continue;
            }

            Vector3 normal =
                Vector3.Normalize(cross);

            builder.AddTriangle(
                materialId,
                v0,
                v1,
                v2,
                normal);
        }
    }

    /// <summary>
    /// Converts Revit's right-handed Z-up coordinate system in internal feet
    /// to glTF's right-handed Y-up coordinate system in metres.
    ///
    /// Revit (X, Y, Z) -> glTF (X, Z, -Y).
    /// The transform has determinant +1, so triangle winding is preserved.
    /// </summary>
    private static Vector3 ToGltfPosition(
        XYZ point)
    {
        return new Vector3(
            CheckedFloat(point.X * FeetToMetres),
            CheckedFloat(point.Z * FeetToMetres),
            CheckedFloat(-point.Y * FeetToMetres));
    }

    private static float CheckedFloat(
        double value)
    {
        if (!double.IsFinite(value) ||
            value < -float.MaxValue ||
            value > float.MaxValue)
        {
            throw new InvalidOperationException(
                "Revit geometry contains a coordinate that cannot be represented by glTF float32.");
        }

        return (float)value;
    }

    private sealed class ElementMeshBuilder
    {
        private readonly Document _document;
        private readonly Element _element;
        private readonly MaterialRegistry _materials;

        private readonly Dictionary<
            string,
            PrimitiveBuilder> _primitives =
                new(StringComparer.Ordinal);

        public ElementMeshBuilder(
            Document document,
            Element element,
            MaterialRegistry materials)
        {
            _document = document;
            _element = element;
            _materials = materials;
        }

        public void AddTriangle(
            ElementId materialId,
            Vector3 p0,
            Vector3 p1,
            Vector3 p2,
            Vector3 normal)
        {
            RevitExportMaterial material =
                _materials.Resolve(
                    materialId,
                    _element);

            if (!_primitives.TryGetValue(
                    material.Key,
                    out PrimitiveBuilder? primitive))
            {
                primitive = new PrimitiveBuilder(
                    material.Key);

                _primitives.Add(
                    material.Key,
                    primitive);
            }

            primitive.AddTriangle(
                p0,
                p1,
                p2,
                normal);
        }

        public RevitExportElement? Build()
        {
            RevitExportPrimitive[] primitives =
                _primitives
                    .Values
                    .Select(
                        static primitive =>
                            primitive.Build())
                    .Where(
                        static primitive =>
                            primitive.Indices.Count > 0)
                    .ToArray();

            if (primitives.Length == 0)
            {
                return null;
            }

            ElementId typeId =
                _element.GetTypeId();

            string typeName =
                string.Empty;

            if (typeId !=
                ElementId.InvalidElementId)
            {
                try
                {
                    typeName =
                        _document
                            .GetElement(typeId)?
                            .Name ??
                        string.Empty;
                }
                catch (Autodesk.Revit.Exceptions.ArgumentException)
                {
                    typeName =
                        string.Empty;
                }
            }

            string uniqueId;

            try
            {
                uniqueId =
                    _element.UniqueId ??
                    string.Empty;
            }
            catch (Autodesk.Revit.Exceptions.InvalidOperationException)
            {
                uniqueId =
                    string.Empty;
            }

            string id =
                _element.Id.Value.ToString(
                    CultureInfo.InvariantCulture);

            string displayName;

            try
            {
                displayName =
                    _element.Name ??
                    string.Empty;
            }
            catch (Autodesk.Revit.Exceptions.InvalidOperationException)
            {
                displayName =
                    string.Empty;
            }

            string category =
                _element.Category?.Name ??
                string.Empty;

            return new RevitExportElement(
                ElementId: id,
                UniqueId: uniqueId,
                DisplayName: displayName,
                Category: category,
                TypeName: typeName,
                Primitives:
                    new ReadOnlyCollection<RevitExportPrimitive>(
                        primitives));
        }
    }

    private sealed class PrimitiveBuilder
    {
        private readonly List<Vector3> _positions =
            new();

        private readonly List<Vector3> _normals =
            new();

        private readonly List<uint> _indices =
            new();

        public PrimitiveBuilder(
            string materialKey)
        {
            MaterialKey =
                materialKey;
        }

        public string MaterialKey { get; }

        public void AddTriangle(
            Vector3 p0,
            Vector3 p1,
            Vector3 p2,
            Vector3 normal)
        {
            if ((ulong)_positions.Count >
                uint.MaxValue - 3UL)
            {
                throw new InvalidOperationException(
                    "A single Revit material primitive exceeded the glTF uint32 index limit.");
            }

            uint start =
                checked((uint)_positions.Count);

            _positions.Add(p0);
            _positions.Add(p1);
            _positions.Add(p2);

            _normals.Add(normal);
            _normals.Add(normal);
            _normals.Add(normal);

            _indices.Add(start);
            _indices.Add(start + 1);
            _indices.Add(start + 2);
        }

        public RevitExportPrimitive Build()
        {
            return new RevitExportPrimitive(
                MaterialKey: MaterialKey,
                Positions:
                    new ReadOnlyCollection<Vector3>(
                        _positions.ToArray()),
                Normals:
                    new ReadOnlyCollection<Vector3>(
                        _normals.ToArray()),
                Indices:
                    new ReadOnlyCollection<uint>(
                        _indices.ToArray()));
        }
    }

    private sealed class MaterialRegistry
    {
        private readonly Document _document;

        private readonly Dictionary<
            string,
            RevitExportMaterial> _materials =
                new(StringComparer.Ordinal);

        public MaterialRegistry(
            Document document)
        {
            _document = document;
        }

        public RevitExportMaterial Resolve(
            ElementId materialId,
            Element owner)
        {
            if (materialId !=
                ElementId.InvalidElementId)
            {
                string key =
                    $"revit-material:{materialId.Value.ToString(CultureInfo.InvariantCulture)}";

                if (_materials.TryGetValue(
                        key,
                        out RevitExportMaterial? cached))
                {
                    return cached;
                }

                Material? material =
                    _document.GetElement(materialId)
                    as Material;

                if (material is not null)
                {
                    RevitExportMaterial created =
                        FromRevitMaterial(
                            key,
                            material);

                    _materials.Add(
                        key,
                        created);

                    return created;
                }
            }

            long categoryId =
                owner.Category?.Id.Value ??
                -1L;

            string fallbackKey =
                $"category:{categoryId.ToString(CultureInfo.InvariantCulture)}";

            if (_materials.TryGetValue(
                    fallbackKey,
                    out RevitExportMaterial? fallback))
            {
                return fallback;
            }

            RevitExportMaterial generated =
                FromCategory(
                    fallbackKey,
                    owner.Category);

            _materials.Add(
                fallbackKey,
                generated);

            return generated;
        }

        public IReadOnlyList<RevitExportMaterial>
            Snapshot()
        {
            return new ReadOnlyCollection<
                RevitExportMaterial>(
                    _materials
                        .Values
                        .OrderBy(
                            static material =>
                                material.Key,
                            StringComparer.Ordinal)
                        .ToArray());
        }

        private static RevitExportMaterial
            FromRevitMaterial(
                string key,
                Material material)
        {
            Vector4 color = new(
                0.72f,
                0.72f,
                0.72f,
                1.0f);

            try
            {
                Autodesk.Revit.DB.Color revitColor =
                    material.Color;

                float alpha = Math.Clamp(
                    1.0f -
                    material.Transparency / 100.0f,
                    0.0f,
                    1.0f);

                color = new Vector4(
                    SrgbByteToUnit(revitColor.Red),
                    SrgbByteToUnit(revitColor.Green),
                    SrgbByteToUnit(revitColor.Blue),
                    alpha);
            }
            catch (Autodesk.Revit.Exceptions.InvalidOperationException)
            {
                // Keep deterministic neutral fallback.
            }

            return new RevitExportMaterial(
                Key: key,
                Name:
                    material.Name ??
                    key,
                BaseColor: color);
        }

        private static RevitExportMaterial
            FromCategory(
                string key,
                Category? category)
        {
            Vector4 color = new(
                0.72f,
                0.72f,
                0.72f,
                1.0f);

            if (category is not null)
            {
                try
                {
                    Autodesk.Revit.DB.Color lineColor =
                        category.LineColor;

                    color = new Vector4(
                        SrgbByteToUnit(lineColor.Red),
                        SrgbByteToUnit(lineColor.Green),
                        SrgbByteToUnit(lineColor.Blue),
                        1.0f);
                }
                catch (Autodesk.Revit.Exceptions.InvalidOperationException)
                {
                    // Keep deterministic neutral fallback.
                }
            }

            return new RevitExportMaterial(
                Key: key,
                Name:
                    category?.Name ??
                    "BIMAP Default",
                BaseColor: color);
        }

        private static float SrgbByteToUnit(
            byte value)
        {
            return value / 255.0f;
        }
    }
}

internal sealed record RevitExportResult(
    string SourceDocumentTitle,
    string SourceDocumentPath,
    string SourceKind,
    string RevitVersion,
    DateTimeOffset ExportedAtUtc,
    IReadOnlyList<RevitExportElement> Elements,
    IReadOnlyList<RevitExportMaterial> Materials,
    long TriangleCount,
    long VertexCount);

internal sealed record RevitExportElement(
    string ElementId,
    string UniqueId,
    string DisplayName,
    string Category,
    string TypeName,
    IReadOnlyList<RevitExportPrimitive> Primitives)
{
    public long TriangleCount =>
        Primitives.Sum(
            static primitive =>
                primitive.Indices.Count / 3L);

    public long VertexCount =>
        Primitives.Sum(
            static primitive =>
                primitive.Positions.Count);
}

internal sealed record RevitExportPrimitive(
    string MaterialKey,
    IReadOnlyList<Vector3> Positions,
    IReadOnlyList<Vector3> Normals,
    IReadOnlyList<uint> Indices);

internal sealed record RevitExportMaterial(
    string Key,
    string Name,
    Vector4 BaseColor);
