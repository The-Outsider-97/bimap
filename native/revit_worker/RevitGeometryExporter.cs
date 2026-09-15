using System.Globalization;
using Autodesk.Revit.DB;

namespace Bimap.RevitWorker.AppBundle;

public static class RevitGeometryExporter
{
    private const double FeetToMetres = 0.3048;
    private const double CubicFeetToCubicMetres = 0.028316846592;
    private const double VertexQuantization = 1_000_000.0;

    public static ExportModel Export(Document document)
    {
        ArgumentNullException.ThrowIfNull(document);

        var options = new Options
        {
            ComputeReferences = false,
            IncludeNonVisibleObjects = false,
            DetailLevel = ViewDetailLevel.Fine,
        };

        var elements = new List<ExportElement>();
        var materialIds = new HashSet<long>();
        var collector = new FilteredElementCollector(document)
            .WhereElementIsNotElementType();

        foreach (var element in collector)
        {
            if (!ShouldExport(element))
            {
                continue;
            }

            GeometryElement? geometry;
            try
            {
                geometry = element.get_Geometry(options);
            }
            catch
            {
                continue;
            }
            if (geometry is null)
            {
                continue;
            }

            var accumulator = new ElementAccumulator(element);
            TraverseGeometry(
                document,
                geometry,
                Transform.Identity,
                accumulator,
                materialIds);

            var exported = accumulator.Build();
            if (exported is not null)
            {
                elements.Add(exported);
            }
        }

        var materials = BuildMaterials(document, materialIds);
        return new ExportModel(elements, materials, GeometrySummary.FromElements(elements));
    }

    public static void ExportPreviewPng(Document document, string destinationPath)
    {
        ArgumentNullException.ThrowIfNull(document);
        if (string.IsNullOrWhiteSpace(destinationPath))
        {
            throw new ArgumentException("A preview destination is required.", nameof(destinationPath));
        }

        var view = FindOrCreateThreeDimensionalView(document);
        var directory = Path.Combine(Path.GetTempPath(), $"bimap-revit-preview-{Guid.NewGuid():N}");
        Directory.CreateDirectory(directory);
        try
        {
            var prefix = Path.Combine(directory, "preview");
            var options = new ImageExportOptions
            {
                ExportRange = ExportRange.SetOfViews,
                FilePath = prefix,
                FitDirection = FitDirectionType.Horizontal,
                HLRandWFViewsFileType = ImageFileType.PNG,
                ShadowViewsFileType = ImageFileType.PNG,
                ImageResolution = ImageResolution.DPI_150,
                PixelSize = 1600,
                ZoomType = ZoomFitType.FitToPage,
            };
            options.SetViewsAndSheets(new List<ElementId> { view.Id });
            document.ExportImage(options);

            var generated = Directory
                .EnumerateFiles(directory, "*.png", SearchOption.TopDirectoryOnly)
                .Select(path => new FileInfo(path))
                .Where(info => info.Length > 0)
                .OrderByDescending(info => info.LastWriteTimeUtc)
                .FirstOrDefault();

            if (generated is null)
            {
                throw new InvalidOperationException("Revit did not produce a PNG preview for the selected 3D view.");
            }

            File.Copy(generated.FullName, destinationPath, overwrite: true);
        }
        finally
        {
            try
            {
                Directory.Delete(directory, recursive: true);
            }
            catch
            {
                // The APS job folder is ephemeral; preview cleanup is best-effort.
            }
        }
    }

    private static bool ShouldExport(Element element)
    {
        if (element is View or ElementType or Material or Level or Grid or ProjectInfo)
        {
            return false;
        }
        return element.Id != ElementId.InvalidElementId;
    }

    private static void TraverseGeometry(
        Document document,
        GeometryElement geometry,
        Transform transform,
        ElementAccumulator accumulator,
        ISet<long> materialIds)
    {
        foreach (var geometryObject in geometry)
        {
            switch (geometryObject)
            {
                case Solid solid when solid.Faces.Size > 0:
                    accumulator.AddSolid(document, solid, transform, materialIds);
                    break;

                case Mesh mesh when mesh.NumTriangles > 0:
                    accumulator.AddMesh(mesh, transform, materialId: -1, materialIds);
                    break;

                case GeometryInstance instance:
                    var nested = instance.GetSymbolGeometry();
                    if (nested is not null)
                    {
                        TraverseGeometry(
                            document,
                            nested,
                            transform.Multiply(instance.Transform),
                            accumulator,
                            materialIds);
                    }
                    break;
            }
        }
    }

    private static IReadOnlyDictionary<long, ExportMaterial> BuildMaterials(
        Document document,
        IEnumerable<long> ids)
    {
        var materials = new Dictionary<long, ExportMaterial>();
        materials[-1] = ExportMaterial.Default;

        foreach (var idValue in ids.Where(value => value >= 0).Distinct())
        {
            Material? material = null;
            try
            {
                material = document.GetElement(new ElementId(idValue)) as Material;
            }
            catch
            {
                // Missing/deleted material references fall back to the neutral material.
            }

            if (material is null)
            {
                continue;
            }

            var color = material.Color;
            var alpha = Math.Clamp(1.0 - (material.Transparency / 100.0), 0.0, 1.0);
            materials[idValue] = new ExportMaterial(
                idValue,
                string.IsNullOrWhiteSpace(material.Name) ? $"Material {idValue}" : material.Name,
                color.Red / 255.0,
                color.Green / 255.0,
                color.Blue / 255.0,
                alpha);
        }

        return materials;
    }

    private static View3D FindOrCreateThreeDimensionalView(Document document)
    {
        var existing = new FilteredElementCollector(document)
            .OfClass(typeof(View3D))
            .Cast<View3D>()
            .FirstOrDefault(view => !view.IsTemplate && view.CanBePrinted);
        if (existing is not null)
        {
            return existing;
        }

        var type = new FilteredElementCollector(document)
            .OfClass(typeof(ViewFamilyType))
            .Cast<ViewFamilyType>()
            .FirstOrDefault(candidate => candidate.ViewFamily == ViewFamily.ThreeDimensional)
            ?? throw new InvalidOperationException("The Revit document has no 3D ViewFamilyType.");

        using var transaction = new Transaction(document, "BIMAP temporary 3D preview view");
        transaction.Start();
        var created = View3D.CreateIsometric(document, type.Id);
        transaction.Commit();
        return created;
    }

    internal static Vector3f ToGltfPoint(XYZ point)
    {
        // Revit is right-handed Z-up in feet. glTF is right-handed Y-up in metres.
        // (X, Y, Z) -> (X, Z, -Y) preserves handedness.
        return new Vector3f(
            (float)(point.X * FeetToMetres),
            (float)(point.Z * FeetToMetres),
            (float)(-point.Y * FeetToMetres));
    }

    internal static Vector3f ToGltfDirection(XYZ vector)
    {
        var transformed = new Vector3f((float)vector.X, (float)vector.Z, (float)-vector.Y);
        return transformed.Normalized();
    }

    internal static PositionKey PositionKeyFor(Vector3f point) => new(
        Quantize(point.X),
        Quantize(point.Y),
        Quantize(point.Z));

    private static long Quantize(float value) => checked((long)Math.Round(value * VertexQuantization));

    private sealed class ElementAccumulator
    {
        private readonly Element _element;
        private readonly Dictionary<long, PrimitiveBuilder> _primitives = new();
        private readonly Dictionary<EdgeKey, int> _topologyEdges = new();
        private double _volumeCubicMetres;
        private long _triangleCount;

        internal ElementAccumulator(Element element)
        {
            _element = element;
        }

        internal void AddSolid(
            Document document,
            Solid solid,
            Transform transform,
            ISet<long> materialIds)
        {
            if (solid.Volume > 0)
            {
                _volumeCubicMetres += solid.Volume * CubicFeetToCubicMetres;
            }

            foreach (Face face in solid.Faces)
            {
                var materialId = face.MaterialElementId?.Value ?? -1;
                AddMesh(face.Triangulate(), transform, materialId, materialIds);
            }
        }

        internal void AddMesh(
            Mesh mesh,
            Transform transform,
            long materialId,
            ISet<long> materialIds)
        {
            if (materialId >= 0)
            {
                materialIds.Add(materialId);
            }
            var primitive = _primitives.TryGetValue(materialId, out var existing)
                ? existing
                : (_primitives[materialId] = new PrimitiveBuilder(materialId));

            for (var index = 0; index < mesh.NumTriangles; index++)
            {
                var triangle = mesh.get_Triangle(index);
                var p0 = ToGltfPoint(transform.OfPoint(triangle.get_Vertex(0)));
                var p1 = ToGltfPoint(transform.OfPoint(triangle.get_Vertex(1)));
                var p2 = ToGltfPoint(transform.OfPoint(triangle.get_Vertex(2)));

                var normal = Vector3f.Cross(p1 - p0, p2 - p0).Normalized();
                if (normal.LengthSquared < 1e-12f)
                {
                    continue;
                }

                primitive.AddTriangle(p0, p1, p2, normal);
                AddTopologyEdge(p0, p1);
                AddTopologyEdge(p1, p2);
                AddTopologyEdge(p2, p0);
                _triangleCount++;
            }
        }

        private void AddTopologyEdge(Vector3f left, Vector3f right)
        {
            var edge = EdgeKey.Create(PositionKeyFor(left), PositionKeyFor(right));
            _topologyEdges[edge] = _topologyEdges.TryGetValue(edge, out var count) ? count + 1 : 1;
        }

        internal ExportElement? Build()
        {
            if (_triangleCount == 0)
            {
                return null;
            }

            var typeName = string.Empty;
            try
            {
                var typeId = _element.GetTypeId();
                if (typeId != ElementId.InvalidElementId)
                {
                    typeName = _element.Document.GetElement(typeId)?.Name ?? string.Empty;
                }
            }
            catch
            {
                // Type metadata is supplemental to geometry identity.
            }

            var watertight = _topologyEdges.Count > 0 && _topologyEdges.Values.All(count => count == 2);
            var primitives = _primitives.Values
                .Select(builder => builder.Build())
                .Where(primitive => primitive.Indices.Count > 0)
                .ToArray();
            var vertexCount = primitives.Sum(primitive => (long)primitive.Positions.Count);

            return new ExportElement(
                _element.Id.Value.ToString(CultureInfo.InvariantCulture),
                _element.UniqueId,
                string.IsNullOrWhiteSpace(_element.Name) ? _element.GetType().Name : _element.Name,
                _element.Category?.Name ?? string.Empty,
                typeName,
                primitives,
                new ElementGeometryMetrics(
                    _triangleCount,
                    vertexCount,
                    _topologyEdges.Count,
                    _volumeCubicMetres,
                    watertight));
        }
    }

    private sealed class PrimitiveBuilder
    {
        private readonly long _materialId;
        private readonly Dictionary<VertexKey, uint> _indexByVertex = new();
        private readonly List<Vector3f> _positions = new();
        private readonly List<Vector3f> _normals = new();
        private readonly List<uint> _indices = new();

        internal PrimitiveBuilder(long materialId) => _materialId = materialId;

        internal void AddTriangle(Vector3f p0, Vector3f p1, Vector3f p2, Vector3f normal)
        {
            _indices.Add(AddVertex(p0, normal));
            _indices.Add(AddVertex(p1, normal));
            _indices.Add(AddVertex(p2, normal));
        }

        private uint AddVertex(Vector3f position, Vector3f normal)
        {
            var key = new VertexKey(PositionKeyFor(position), PositionKeyFor(normal));
            if (_indexByVertex.TryGetValue(key, out var existing))
            {
                return existing;
            }

            var index = checked((uint)_positions.Count);
            _indexByVertex[key] = index;
            _positions.Add(position);
            _normals.Add(normal);
            return index;
        }

        internal PrimitiveGeometry Build() => new(
            _materialId,
            _positions.ToArray(),
            _normals.ToArray(),
            _indices.ToArray());
    }
}

public sealed record ExportModel(
    IReadOnlyList<ExportElement> Elements,
    IReadOnlyDictionary<long, ExportMaterial> Materials,
    GeometrySummary Summary);

public sealed record ExportElement(
    string ElementId,
    string UniqueId,
    string Name,
    string Category,
    string TypeName,
    IReadOnlyList<PrimitiveGeometry> Primitives,
    ElementGeometryMetrics Metrics);

public sealed record PrimitiveGeometry(
    long MaterialId,
    IReadOnlyList<Vector3f> Positions,
    IReadOnlyList<Vector3f> Normals,
    IReadOnlyList<uint> Indices);

public sealed record ElementGeometryMetrics(
    long TotalPolygons,
    long TotalVertices,
    long TotalUniqueEdges,
    double Volume,
    bool Watertight);

public sealed record GeometrySummary(
    long TotalPolygons,
    long TotalVertices,
    long TotalUniqueEdges,
    double Volume,
    long WatertightGeometries)
{
    public static GeometrySummary FromElements(IEnumerable<ExportElement> elements)
    {
        var rows = elements.ToArray();
        return new GeometrySummary(
            rows.Sum(row => row.Metrics.TotalPolygons),
            rows.Sum(row => row.Metrics.TotalVertices),
            rows.Sum(row => row.Metrics.TotalUniqueEdges),
            rows.Sum(row => row.Metrics.Volume),
            rows.LongCount(row => row.Metrics.Watertight));
    }
}

public sealed record ExportMaterial(
    long Id,
    string Name,
    double Red,
    double Green,
    double Blue,
    double Alpha)
{
    public static ExportMaterial Default { get; } = new(
        -1,
        "BIMAP Default",
        0.72,
        0.74,
        0.76,
        1.0);
}

public readonly record struct Vector3f(float X, float Y, float Z)
{
    public float LengthSquared => X * X + Y * Y + Z * Z;

    public Vector3f Normalized()
    {
        var length = MathF.Sqrt(LengthSquared);
        return length <= 1e-12f ? new Vector3f(0, 1, 0) : new Vector3f(X / length, Y / length, Z / length);
    }

    public static Vector3f operator -(Vector3f left, Vector3f right) =>
        new(left.X - right.X, left.Y - right.Y, left.Z - right.Z);

    public static Vector3f Cross(Vector3f left, Vector3f right) => new(
        left.Y * right.Z - left.Z * right.Y,
        left.Z * right.X - left.X * right.Z,
        left.X * right.Y - left.Y * right.X);
}

internal readonly record struct PositionKey(long X, long Y, long Z) : IComparable<PositionKey>
{
    public int CompareTo(PositionKey other)
    {
        var x = X.CompareTo(other.X);
        if (x != 0) return x;
        var y = Y.CompareTo(other.Y);
        return y != 0 ? y : Z.CompareTo(other.Z);
    }
}

internal readonly record struct VertexKey(PositionKey Position, PositionKey Normal);

internal readonly record struct EdgeKey(PositionKey A, PositionKey B)
{
    internal static EdgeKey Create(PositionKey left, PositionKey right) =>
        left.CompareTo(right) <= 0 ? new EdgeKey(left, right) : new EdgeKey(right, left);
}
