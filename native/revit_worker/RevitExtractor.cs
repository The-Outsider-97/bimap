using System.Globalization;
using System.Reflection;
using Autodesk.Revit.DB;

namespace Bimap.RevitWorker.AppBundle;

public static class RevitExtractor
{
    private const string ExtensionSchemaVersion = "1.0.0";
    private const string ExtractorVersion = "bimap-native-revit-1.0.0";

    public static IReadOnlyDictionary<string, object?> Inspect(Document document, string sourceFormat)
    {
        ArgumentNullException.ThrowIfNull(document);
        return new Dictionary<string, object?>
        {
            ["source_format"] = sourceFormat,
            ["schema"] = $"Autodesk Revit {document.Application.VersionNumber}",
            ["product_count"] = ProductCount(document),
            ["project_name"] = ResolveProjectName(document),
        };
    }

    public static IReadOnlyDictionary<string, object?> Extract(
        Document document,
        string sourceFormat,
        IReadOnlyCollection<string> requestedDatasets)
    {
        ArgumentNullException.ThrowIfNull(document);
        ArgumentNullException.ThrowIfNull(requestedDatasets);

        var geometry = RevitGeometryExporter.Export(document);
        var elements = BuildElementRows(document, geometry);
        var properties = BuildPropertyRows(document);
        var quantities = BuildQuantityRows(geometry);
        var materials = BuildMaterialRows(document);

        var datasets = new Dictionary<string, object?>
        {
            ["elements"] = requestedDatasets.Contains("elements", StringComparer.Ordinal)
                ? elements
                : Array.Empty<object>(),
            ["properties"] = requestedDatasets.Contains("properties", StringComparer.Ordinal)
                ? properties
                : Array.Empty<object>(),
            ["quantities"] = requestedDatasets.Contains("quantities", StringComparer.Ordinal)
                ? quantities
                : Array.Empty<object>(),
            ["materials"] = requestedDatasets.Contains("materials", StringComparer.Ordinal)
                ? materials
                : Array.Empty<object>(),
        };

        var counts = new Dictionary<string, int>
        {
            ["elements"] = requestedDatasets.Contains("elements", StringComparer.Ordinal) ? elements.Count : 0,
            ["properties"] = requestedDatasets.Contains("properties", StringComparer.Ordinal) ? properties.Count : 0,
            ["quantities"] = requestedDatasets.Contains("quantities", StringComparer.Ordinal) ? quantities.Count : 0,
            ["materials"] = requestedDatasets.Contains("materials", StringComparer.Ordinal) ? materials.Count : 0,
        };

        var project = BuildProjectRecord(document, sourceFormat);
        var extensions = new Dictionary<string, object?>();
        if (string.Equals(sourceFormat, "rfa", StringComparison.Ordinal))
        {
            extensions["revit_family"] = BuildFamilyExtension(document, geometry);
        }

        return new Dictionary<string, object?>
        {
            ["inspection"] = Inspect(document, sourceFormat),
            ["project"] = project,
            ["units"] = new object[]
            {
                new Dictionary<string, object?>
                {
                    ["source"] = "Revit",
                    ["symbol"] = "m",
                    ["geometry_length_unit"] = "metre",
                    ["geometry_volume_unit"] = "cubic_metre",
                },
            },
            ["datasets"] = datasets,
            ["counts"] = counts,
            ["ifc_class_counts"] = new Dictionary<string, int>(),
            ["geometry_summary"] = GeometrySummaryRecord(geometry.Summary),
            ["extensions"] = extensions,
        };
    }

    private static IReadOnlyDictionary<string, object?> BuildProjectRecord(Document document, string sourceFormat)
    {
        var record = new Dictionary<string, object?>
        {
            ["source_format"] = sourceFormat,
            ["project_name"] = ResolveProjectName(document),
            ["source_revit_version"] = ResolveSourceRevitVersion(document),
            ["revit_engine_version"] = document.Application.VersionNumber,
            ["extraction_mode"] = "native_revit_api",
        };

        if (document.IsFamilyDocument)
        {
            record["family_name"] = document.OwnerFamily?.Name ?? ResolveProjectName(document);
            record["category"] = document.OwnerFamily?.FamilyCategory?.Name;
        }
        return record;
    }

    private static IReadOnlyDictionary<string, object?> BuildFamilyExtension(
        Document document,
        ExportModel geometry)
    {
        if (!document.IsFamilyDocument)
        {
            throw new InvalidOperationException("A revit_family extension can only be generated for a Revit family document.");
        }

        var familyManager = document.FamilyManager;
        var identity = new Dictionary<string, object?>
        {
            ["family_name"] = document.OwnerFamily?.Name ?? ResolveProjectName(document),
            ["category"] = document.OwnerFamily?.FamilyCategory?.Name,
            ["source_revit_version"] = ResolveSourceRevitVersion(document),
            ["revit_engine_version"] = document.Application.VersionNumber,
        };

        var typeCatalog = BuildFamilyTypeRows(familyManager);
        var parameters = BuildFamilyParameterRows(familyManager);
        var formulas = parameters
            .Where(row => row.TryGetValue("formula", out var formula)
                          && formula is string text
                          && !string.IsNullOrWhiteSpace(text))
            .Select(row => new Dictionary<string, object?>
            {
                ["parameter_name"] = row["name"],
                ["formula"] = row["formula"],
                ["is_instance"] = row["is_instance"],
            })
            .Cast<object>()
            .ToArray();
        var materials = BuildMaterialRows(document).Cast<object>().ToArray();
        var connectors = BuildConnectorRows(document).Cast<object>().ToArray();
        var nested = BuildNestedComponentRows(document).Cast<object>().ToArray();
        var metrics = BuildFamilyGeometryMetricRows(geometry).Cast<object>().ToArray();

        return new Dictionary<string, object?>
        {
            ["schema_version"] = ExtensionSchemaVersion,
            ["extractor_version"] = ExtractorVersion,
            ["revit_engine_version"] = document.Application.VersionNumber,
            ["source_revit_version"] = ResolveSourceRevitVersion(document),
            ["sections_assessed"] = new[]
            {
                "identity",
                "type_catalog",
                "parameters",
                "formulas",
                "materials",
                "connectors",
                "nested_components",
                "geometry_metrics",
                "documentation",
            },
            ["identity"] = identity,
            ["type_catalog"] = typeCatalog.Cast<object>().ToArray(),
            ["parameters"] = parameters.Cast<object>().ToArray(),
            ["formulas"] = formulas,
            ["materials"] = materials,
            ["connectors"] = connectors,
            ["nested_components"] = nested,
            ["geometry_metrics"] = metrics,
            ["documentation"] = new object[]
            {
                new Dictionary<string, object?>
                {
                    ["kind"] = "native_revit_api",
                    ["product"] = "Autodesk Revit",
                    ["extractor_version"] = ExtractorVersion,
                    ["revit_engine_version"] = document.Application.VersionNumber,
                    ["source_revit_version"] = ResolveSourceRevitVersion(document),
                },
            },
        };
    }

    private static List<Dictionary<string, object?>> BuildElementRows(
        Document document,
        ExportModel geometry)
    {
        var metricsById = geometry.Elements.ToDictionary(
            item => item.ElementId,
            item => item.Metrics,
            StringComparer.Ordinal);
        var rows = new List<Dictionary<string, object?>>();

        foreach (var element in new FilteredElementCollector(document).WhereElementIsNotElementType())
        {
            if (element.Id == ElementId.InvalidElementId)
            {
                continue;
            }

            var id = element.Id.Value.ToString(CultureInfo.InvariantCulture);
            rows.Add(new Dictionary<string, object?>
            {
                ["element_id"] = id,
                ["revit_id"] = id,
                ["unique_id"] = element.UniqueId,
                ["name"] = SafeElementName(element),
                ["category"] = element.Category?.Name,
                ["class"] = element.GetType().Name,
                ["type_name"] = ResolveTypeName(document, element),
                ["has_exported_geometry"] = metricsById.ContainsKey(id),
            });
        }

        return rows;
    }

    private static List<Dictionary<string, object?>> BuildPropertyRows(Document document)
    {
        var rows = new List<Dictionary<string, object?>>();
        foreach (var element in new FilteredElementCollector(document).WhereElementIsNotElementType())
        {
            foreach (Parameter parameter in element.Parameters)
            {
                rows.Add(new Dictionary<string, object?>
                {
                    ["element_id"] = element.Id.Value.ToString(CultureInfo.InvariantCulture),
                    ["unique_id"] = element.UniqueId,
                    ["name"] = parameter.Definition?.Name,
                    ["storage_type"] = parameter.StorageType.ToString().ToLowerInvariant(),
                    ["value"] = ReadParameterValue(parameter),
                    ["value_string"] = SafeAsValueString(parameter),
                    ["read_only"] = parameter.IsReadOnly,
                });
            }
        }

        if (document.IsFamilyDocument)
        {
            foreach (var parameter in BuildFamilyParameterRows(document.FamilyManager))
            {
                rows.Add(new Dictionary<string, object?>(parameter)
                {
                    ["scope"] = "family_parameter",
                });
            }
        }
        return rows;
    }

    private static List<Dictionary<string, object?>> BuildQuantityRows(ExportModel geometry) =>
        geometry.Elements
            .Select(element => new Dictionary<string, object?>
            {
                ["element_id"] = element.ElementId,
                ["unique_id"] = element.UniqueId,
                ["total_polygons"] = element.Metrics.TotalPolygons,
                ["total_vertices"] = element.Metrics.TotalVertices,
                ["total_unique_edges"] = element.Metrics.TotalUniqueEdges,
                ["volume"] = element.Metrics.Volume,
                ["volume_unit"] = "m3",
                ["watertight"] = element.Metrics.Watertight,
            })
            .ToList();

    private static List<Dictionary<string, object?>> BuildMaterialRows(Document document)
    {
        var rows = new List<Dictionary<string, object?>>();
        foreach (var material in new FilteredElementCollector(document)
                     .OfClass(typeof(Material))
                     .Cast<Material>())
        {
            var color = material.Color;
            rows.Add(new Dictionary<string, object?>
            {
                ["element_id"] = material.Id.Value.ToString(CultureInfo.InvariantCulture),
                ["name"] = material.Name,
                ["red"] = color.Red,
                ["green"] = color.Green,
                ["blue"] = color.Blue,
                ["transparency_percent"] = material.Transparency,
                ["shininess"] = material.Shininess,
                ["smoothness"] = material.Smoothness,
            });
        }
        return rows;
    }

    private static List<Dictionary<string, object?>> BuildFamilyTypeRows(FamilyManager manager)
    {
        var parameters = EnumerateFamilyParameters(manager).ToArray();
        var rows = new List<Dictionary<string, object?>>();
        foreach (FamilyType type in manager.Types)
        {
            var values = new List<Dictionary<string, object?>>();
            foreach (var parameter in parameters)
            {
                values.Add(new Dictionary<string, object?>
                {
                    ["name"] = parameter.Definition?.Name,
                    ["value"] = ReadFamilyTypeValue(type, parameter),
                });
            }

            rows.Add(new Dictionary<string, object?>
            {
                ["type_name"] = type.Name,
                ["parameter_values"] = values.Cast<object>().ToArray(),
            });
        }
        return rows;
    }

    private static List<Dictionary<string, object?>> BuildFamilyParameterRows(FamilyManager manager)
    {
        var rows = new List<Dictionary<string, object?>>();
        foreach (var parameter in EnumerateFamilyParameters(manager))
        {
            var dataType = string.Empty;
            var unitType = string.Empty;
            try
            {
                dataType = parameter.Definition?.GetDataType().TypeId ?? string.Empty;
                unitType = parameter.GetUnitTypeId().TypeId;
            }
            catch
            {
                // Some non-measurable parameter definitions do not expose a unit type.
            }

            rows.Add(new Dictionary<string, object?>
            {
                ["name"] = parameter.Definition?.Name,
                ["storage_type"] = parameter.StorageType.ToString().ToLowerInvariant(),
                ["data_type"] = dataType,
                ["unit_type"] = unitType,
                ["is_instance"] = parameter.IsInstance,
                ["is_shared"] = parameter.IsShared,
                ["shared_guid"] = parameter.IsShared ? parameter.GUID.ToString("D") : null,
                ["definition_id"] = parameter.Id.Value.ToString(CultureInfo.InvariantCulture),
                ["formula"] = parameter.Formula,
            });
        }
        return rows;
    }

    private static IEnumerable<FamilyParameter> EnumerateFamilyParameters(FamilyManager manager)
    {
        foreach (FamilyParameter parameter in manager.Parameters)
        {
            yield return parameter;
        }
    }

    private static List<Dictionary<string, object?>> BuildConnectorRows(Document document)
    {
        var rows = new List<Dictionary<string, object?>>();
        foreach (var element in new FilteredElementCollector(document).WhereElementIsNotElementType())
        {
            var type = element.GetType();
            if (!type.Name.Contains("Connector", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            rows.Add(new Dictionary<string, object?>
            {
                ["element_id"] = element.Id.Value.ToString(CultureInfo.InvariantCulture),
                ["unique_id"] = element.UniqueId,
                ["name"] = SafeElementName(element),
                ["category"] = element.Category?.Name,
                ["connector_class"] = type.FullName,
                ["domain"] = ReadReflectedValue(element, "Domain"),
                ["connector_type"] = ReadReflectedValue(element, "ConnectorType"),
                ["system_classification"] = ReadReflectedValue(element, "SystemClassification"),
            });
        }
        return rows;
    }

    private static List<Dictionary<string, object?>> BuildNestedComponentRows(Document document)
    {
        var rows = new List<Dictionary<string, object?>>();
        foreach (var instance in new FilteredElementCollector(document)
                     .OfClass(typeof(FamilyInstance))
                     .Cast<FamilyInstance>())
        {
            if (instance.SuperComponent is null && !document.IsFamilyDocument)
            {
                continue;
            }

            rows.Add(new Dictionary<string, object?>
            {
                ["element_id"] = instance.Id.Value.ToString(CultureInfo.InvariantCulture),
                ["unique_id"] = instance.UniqueId,
                ["name"] = SafeElementName(instance),
                ["family_name"] = instance.Symbol?.FamilyName,
                ["type_name"] = instance.Symbol?.Name,
                ["category"] = instance.Category?.Name,
                ["super_component_id"] = instance.SuperComponent?.Id.Value.ToString(CultureInfo.InvariantCulture),
            });
        }
        return rows;
    }

    private static List<Dictionary<string, object?>> BuildFamilyGeometryMetricRows(ExportModel geometry)
    {
        return new List<Dictionary<string, object?>>
        {
            Metric("total_polygons", geometry.Summary.TotalPolygons, "triangles"),
            Metric("total_vertices", geometry.Summary.TotalVertices, "vertices"),
            Metric("total_unique_edges", geometry.Summary.TotalUniqueEdges, "edges"),
            Metric("volume", geometry.Summary.Volume, "m3"),
            Metric("watertight_geometries", geometry.Summary.WatertightGeometries, "count"),
            Metric("geometry_element_count", geometry.Elements.Count, "count"),
        };
    }

    private static Dictionary<string, object?> Metric(string name, object value, string unit) =>
        new()
        {
            ["metric"] = name,
            ["value"] = value,
            ["unit"] = unit,
        };

    private static IReadOnlyDictionary<string, object?> GeometrySummaryRecord(GeometrySummary summary) =>
        new Dictionary<string, object?>
        {
            ["total_polygons"] = summary.TotalPolygons,
            ["total_vertices"] = summary.TotalVertices,
            ["total_unique_edges"] = summary.TotalUniqueEdges,
            ["volume"] = summary.Volume,
            ["volume_unit"] = "m3",
            ["watertight_geometries"] = summary.WatertightGeometries,
        };

    private static int ProductCount(Document document)
    {
        if (document.IsFamilyDocument)
        {
            return 1;
        }
        return new FilteredElementCollector(document)
            .WhereElementIsNotElementType()
            .ToElementIds()
            .Count;
    }

    private static string ResolveProjectName(Document document)
    {
        if (document.IsFamilyDocument && !string.IsNullOrWhiteSpace(document.OwnerFamily?.Name))
        {
            return document.OwnerFamily.Name;
        }
        return string.IsNullOrWhiteSpace(document.Title) ? "Revit model" : document.Title;
    }

    private static string ResolveSourceRevitVersion(Document document)
    {
        try
        {
            if (!string.IsNullOrWhiteSpace(document.PathName) && File.Exists(document.PathName))
            {
                var info = BasicFileInfo.Extract(document.PathName);
                if (!string.IsNullOrWhiteSpace(info.Format))
                {
                    return info.Format;
                }
            }
        }
        catch
        {
            // Engine version remains available even if file metadata cannot be read.
        }
        return document.Application.VersionNumber;
    }

    private static string SafeElementName(Element element)
    {
        try
        {
            return string.IsNullOrWhiteSpace(element.Name) ? element.GetType().Name : element.Name;
        }
        catch
        {
            return element.GetType().Name;
        }
    }

    private static string? ResolveTypeName(Document document, Element element)
    {
        try
        {
            var typeId = element.GetTypeId();
            if (typeId == ElementId.InvalidElementId)
            {
                return null;
            }
            return document.GetElement(typeId)?.Name;
        }
        catch
        {
            return null;
        }
    }

    private static object? ReadParameterValue(Parameter parameter)
    {
        try
        {
            return parameter.StorageType switch
            {
                StorageType.String => parameter.AsString(),
                StorageType.Double => parameter.AsDouble(),
                StorageType.Integer => parameter.AsInteger(),
                StorageType.ElementId => parameter.AsElementId()?.Value,
                _ => null,
            };
        }
        catch
        {
            return null;
        }
    }

    private static string? SafeAsValueString(Parameter parameter)
    {
        try
        {
            return parameter.AsValueString();
        }
        catch
        {
            return null;
        }
    }

    private static object? ReadFamilyTypeValue(FamilyType type, FamilyParameter parameter)
    {
        try
        {
            return parameter.StorageType switch
            {
                StorageType.String => type.AsString(parameter),
                StorageType.Double => type.AsDouble(parameter),
                StorageType.Integer => type.AsInteger(parameter),
                StorageType.ElementId => type.AsElementId(parameter)?.Value,
                _ => null,
            };
        }
        catch
        {
            return null;
        }
    }

    private static object? ReadReflectedValue(object value, string propertyName)
    {
        try
        {
            var property = value.GetType().GetProperty(
                propertyName,
                BindingFlags.Public | BindingFlags.Instance);
            var result = property?.GetValue(value);
            return result switch
            {
                null => null,
                ElementId id => id.Value,
                string text => text,
                bool boolean => boolean,
                byte number => number,
                short number => number,
                int number => number,
                long number => number,
                float number => number,
                double number => number,
                decimal number => number,
                Enum enumeration => enumeration.ToString(),
                _ => result.ToString(),
            };
        }
        catch
        {
            return null;
        }
    }
}
