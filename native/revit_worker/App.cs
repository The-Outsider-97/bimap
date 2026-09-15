using System.Text.Json;
using System.Text.Json.Serialization;
using Autodesk.Revit.ApplicationServices;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using DesignAutomationFramework;

namespace Bimap.RevitWorker.AppBundle;

[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class App : IExternalDBApplication
{
    private const string ParameterFileName = "params.json";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = false,
        WriteIndented = false,
    };

    public ExternalDBApplicationResult OnStartup(ControlledApplication application)
    {
        DesignAutomationBridge.DesignAutomationReadyEvent += OnDesignAutomationReady;
        return ExternalDBApplicationResult.Succeeded;
    }

    public ExternalDBApplicationResult OnShutdown(ControlledApplication application)
    {
        DesignAutomationBridge.DesignAutomationReadyEvent -= OnDesignAutomationReady;
        return ExternalDBApplicationResult.Succeeded;
    }

    private static void OnDesignAutomationReady(object sender, DesignAutomationReadyEventArgs e)
    {
        try
        {
            Execute(e.DesignAutomationData.RevitDoc);
            e.Succeeded = true;
        }
        catch (Exception ex)
        {
            e.Succeeded = false;
            Console.Error.WriteLine(
                $"BIMAP_REVIT_APPBUNDLE_ERROR[{ex.GetType().Name}]: {Sanitize(ex.Message)}");
        }
    }

    private static void Execute(Document document)
    {
        ArgumentNullException.ThrowIfNull(document);

        var parameterPath = Path.Combine(Environment.CurrentDirectory, ParameterFileName);
        if (!File.Exists(parameterPath))
        {
            throw new InvalidOperationException("The Design Automation job did not provide params.json.");
        }

        var request = JsonSerializer.Deserialize<WorkerParameters>(
            File.ReadAllText(parameterPath),
            JsonOptions) ?? throw new InvalidOperationException("params.json could not be deserialized.");
        request.Validate(document);

        var outputPath = Path.Combine(Environment.CurrentDirectory, request.OutputFile);
        if (File.Exists(outputPath))
        {
            File.Delete(outputPath);
        }

        switch (request.Mode)
        {
            case "inspect":
                WriteJsonAtomically(
                    outputPath,
                    RevitExtractor.Inspect(document, request.SourceFormat));
                break;

            case "extract":
                WriteJsonAtomically(
                    outputPath,
                    RevitExtractor.Extract(document, request.SourceFormat, request.Datasets));
                break;

            case "convert":
                if (!string.Equals(request.TargetFormat, "glb", StringComparison.Ordinal))
                {
                    throw new InvalidOperationException("Only glb conversion is supported by the Revit AppBundle.");
                }
                var model = RevitGeometryExporter.Export(document);
                GlbWriter.Write(outputPath, model, "1.0.0");
                break;

            case "preview":
                RevitGeometryExporter.ExportPreviewPng(document, outputPath);
                break;

            default:
                throw new InvalidOperationException("Unsupported Revit worker mode.");
        }

        var outputInfo = new FileInfo(outputPath);
        if (!outputInfo.Exists || outputInfo.Length <= 0)
        {
            throw new InvalidOperationException("The Revit worker operation produced no output artifact.");
        }
    }

    private static void WriteJsonAtomically(string destination, object payload)
    {
        var temporary = destination + ".tmp";
        try
        {
            using (var stream = new FileStream(
                       temporary,
                       FileMode.Create,
                       FileAccess.Write,
                       FileShare.None))
            {
                JsonSerializer.Serialize(stream, payload, JsonOptions);
                stream.Flush(flushToDisk: true);
            }

            if (File.Exists(destination))
            {
                File.Delete(destination);
            }
            File.Move(temporary, destination);
        }
        finally
        {
            if (File.Exists(temporary))
            {
                File.Delete(temporary);
            }
        }
    }

    private static string Sanitize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "unspecified error";
        }
        var normalized = value.Replace('\r', ' ').Replace('\n', ' ').Trim();
        return normalized[..Math.Min(normalized.Length, 500)];
    }
}

public sealed class WorkerParameters
{
    [JsonPropertyName("mode")]
    public string Mode { get; set; } = string.Empty;

    [JsonPropertyName("source_format")]
    public string SourceFormat { get; set; } = string.Empty;

    [JsonPropertyName("datasets")]
    public string[] Datasets { get; set; } = Array.Empty<string>();

    [JsonPropertyName("target_format")]
    public string? TargetFormat { get; set; }

    [JsonPropertyName("output_stem")]
    public string? OutputStem { get; set; }

    [JsonPropertyName("output_file")]
    public string OutputFile { get; set; } = "result.bin";

    [JsonPropertyName("schema_version")]
    public string SchemaVersion { get; set; } = "1.0.0";

    public void Validate(Document document)
    {
        var mode = Mode.Trim().ToLowerInvariant();
        if (mode is not ("inspect" or "extract" or "convert" or "preview"))
        {
            throw new InvalidOperationException("params.json contains an unsupported mode.");
        }
        Mode = mode;

        var sourceFormat = SourceFormat.Trim().ToLowerInvariant();
        if (sourceFormat is not ("rfa" or "rvt"))
        {
            throw new InvalidOperationException("params.json source_format must be rfa or rvt.");
        }
        if (sourceFormat == "rfa" && !document.IsFamilyDocument)
        {
            throw new InvalidOperationException("The source was declared rfa but Revit opened a non-family document.");
        }
        if (sourceFormat == "rvt" && document.IsFamilyDocument)
        {
            throw new InvalidOperationException("The source was declared rvt but Revit opened a family document.");
        }
        SourceFormat = sourceFormat;

        var allowedDatasets = new HashSet<string>(StringComparer.Ordinal)
        {
            "elements", "properties", "quantities", "materials",
        };
        Datasets = Datasets
            .Select(value => (value ?? string.Empty).Trim().ToLowerInvariant())
            .Where(value => value.Length > 0)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        if (Datasets.Any(value => !allowedDatasets.Contains(value)))
        {
            throw new InvalidOperationException("params.json contains an unsupported extraction dataset.");
        }

        if (!string.Equals(SchemaVersion, "1.0.0", StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Unsupported Revit worker parameter schema version.");
        }

        if (Path.IsPathRooted(OutputFile)
            || OutputFile.Contains(Path.DirectorySeparatorChar)
            || OutputFile.Contains(Path.AltDirectorySeparatorChar)
            || OutputFile is "." or ".."
            || string.IsNullOrWhiteSpace(OutputFile))
        {
            throw new InvalidOperationException("output_file must be a safe local basename.");
        }

        if (mode == "convert")
        {
            TargetFormat = TargetFormat?.Trim().ToLowerInvariant();
            if (TargetFormat != "glb")
            {
                throw new InvalidOperationException("convert mode requires target_format=glb.");
            }
            OutputStem = string.IsNullOrWhiteSpace(OutputStem)
                ? "revit-model"
                : OutputStem.Trim();
        }
    }
}
