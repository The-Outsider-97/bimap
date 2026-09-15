using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Microsoft.Win32;

namespace Bimap.RevitLocalExporter;

/// <summary>
/// Read-only Revit command that exports the currently open model to a
/// BIMAP-compatible glTF 2.0 binary (.glb) viewer artifact.
///
/// The Revit document remains authoritative. The GLB is a derived
/// visualization artifact only; it embeds stable Revit identifiers in glTF
/// node names/extras so BIMAP can resolve findings to Three.js objects.
/// </summary>
[Transaction(TransactionMode.ReadOnly)]
[Regeneration(RegenerationOption.Manual)]
public sealed class ExportCommand : IExternalCommand
{
    private const string ProductName = "R3D BIMAP Revit Local Exporter";

    public Result Execute(
        ExternalCommandData commandData,
        ref string message,
        ElementSet elements)
    {
        ArgumentNullException.ThrowIfNull(commandData);

        UIDocument? uiDocument = commandData.Application.ActiveUIDocument;
        if (uiDocument is null)
        {
            message = "No active Revit document is available.";
            return Result.Failed;
        }

        Document document = uiDocument.Document;

        try
        {
            string defaultFileName = BuildDefaultFileName(document);
            string? outputPath = PromptForOutputPath(
                document,
                defaultFileName);

            if (string.IsNullOrWhiteSpace(outputPath))
            {
                return Result.Cancelled;
            }

            RevitExportResult export =
                RevitGeometryExporter.Export(document);

            if (export.Elements.Count == 0 ||
                export.TriangleCount == 0)
            {
                TaskDialog.Show(
                    ProductName,
                    "No renderable mesh geometry was found in the current Revit model.\n\n" +
                    "The exporter does not fabricate geometry. Check the active family/type, " +
                    "visibility settings, and whether the model contains 3D solid/mesh geometry.");
                return Result.Cancelled;
            }

            GlbWriteResult written =
                GlbWriter.Write(outputPath, export);

            Trace.WriteLine(
                FormattableString.Invariant(
                    $"[BIMAP] GLB export completed: " +
                    $"elements={export.Elements.Count}, " +
                    $"triangles={export.TriangleCount}, " +
                    $"vertices={export.VertexCount}, " +
                    $"bytes={written.SizeBytes}, " +
                    $"sha256={written.Sha256}"));

            TaskDialog dialog = new(ProductName)
            {
                MainInstruction =
                    "BIMAP viewer model exported successfully.",
                MainContent =
                    $"File:\n{written.Path}\n\n" +
                    $"Elements with geometry: {export.Elements.Count.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Triangles: {export.TriangleCount.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Vertices: {export.VertexCount.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Size: {FormatBytes(written.SizeBytes)}\n\n" +
                    "Load this .glb in BIMAP Model Navigator. " +
                    "The original RFA/RVT remains the authoritative audit source.",
                CommonButtons = TaskDialogCommonButtons.Close
            };
            dialog.Show();

            return Result.Succeeded;
        }
        catch (OperationCanceledException)
        {
            return Result.Cancelled;
        }
        catch (Exception exception)
        {
            Trace.TraceError(
                $"[BIMAP] Revit local GLB export failed: {exception}");

            message =
                "BIMAP could not export the current Revit model to GLB. " +
                $"{exception.GetType().Name}: {exception.Message}";

            TaskDialog.Show(ProductName, message);
            return Result.Failed;
        }
    }

    private static string? PromptForOutputPath(
        Document document,
        string defaultFileName)
    {
        SaveFileDialog dialog = new()
        {
            Title = "Export BIMAP viewer model",
            AddExtension = true,
            DefaultExt = ".glb",
            Filter = "glTF Binary (*.glb)|*.glb",
            FileName = defaultFileName,
            OverwritePrompt = true,
            CheckPathExists = true
        };

        string? sourceDirectory =
            TryGetDocumentDirectory(document);

        if (!string.IsNullOrWhiteSpace(sourceDirectory))
        {
            dialog.InitialDirectory = sourceDirectory;
        }

        bool? accepted = dialog.ShowDialog();
        if (accepted != true)
        {
            return null;
        }

        string fullPath =
            Path.GetFullPath(dialog.FileName);

        if (!string.Equals(
                Path.GetExtension(fullPath),
                ".glb",
                StringComparison.OrdinalIgnoreCase))
        {
            fullPath += ".glb";
        }

        return fullPath;
    }

    private static string BuildDefaultFileName(
        Document document)
    {
        string title = document.Title;

        if (string.IsNullOrWhiteSpace(title))
        {
            title = document.IsFamilyDocument
                ? "revit-family"
                : "revit-model";
        }

        string baseName =
            Path.GetFileNameWithoutExtension(title);

        baseName = SanitizeFileName(baseName);

        if (string.IsNullOrWhiteSpace(baseName))
        {
            baseName = document.IsFamilyDocument
                ? "revit-family"
                : "revit-model";
        }

        return $"{baseName}-viewer.glb";
    }

    private static string SanitizeFileName(string value)
    {
        char[] invalid =
            Path.GetInvalidFileNameChars();

        char[] buffer =
            new char[value.Length];

        int length = 0;

        foreach (char character in value)
        {
            bool isInvalid =
                Array.IndexOf(invalid, character) >= 0;

            buffer[length++] =
                isInvalid ? '_' : character;
        }

        return new string(buffer, 0, length).Trim();
    }

    private static string? TryGetDocumentDirectory(
        Document document)
    {
        string path = document.PathName;

        if (!string.IsNullOrWhiteSpace(path))
        {
            try
            {
                string? directory =
                    Path.GetDirectoryName(path);

                if (!string.IsNullOrWhiteSpace(directory) &&
                    Directory.Exists(directory))
                {
                    return directory;
                }
            }
            catch (ArgumentException)
            {
                // Fall through to the user's documents directory.
            }
        }

        string documents =
            Environment.GetFolderPath(
                Environment.SpecialFolder.MyDocuments);

        return Directory.Exists(documents)
            ? documents
            : null;
    }

    private static string FormatBytes(long bytes)
    {
        const double kib = 1024.0;
        const double mib = kib * 1024.0;
        const double gib = mib * 1024.0;

        return bytes switch
        {
            < 1024 =>
                $"{bytes.ToString(CultureInfo.InvariantCulture)} B",

            < 1024L * 1024L =>
                $"{(bytes / kib).ToString("0.0", CultureInfo.InvariantCulture)} KiB",

            < 1024L * 1024L * 1024L =>
                $"{(bytes / mib).ToString("0.0", CultureInfo.InvariantCulture)} MiB",

            _ =>
                $"{(bytes / gib).ToString("0.00", CultureInfo.InvariantCulture)} GiB"
        };
    }
}
