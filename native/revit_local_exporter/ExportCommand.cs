using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
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

            /*
             * For a saved local RFA/RVT, the exported GLB must represent the
             * exact bytes BIMAP will audit. If the Revit document has unsaved
             * changes, hashing Document.PathName would fingerprint the last
             * saved file while the geometry exporter would use the modified
             * in-memory document. Refuse that ambiguous state.
             */
            if (HasUnsavedLocalChanges(document))
            {
                TaskDialog.Show(
                    ProductName,
                    "Save the Revit model before exporting BIMAP viewer geometry.\n\n" +
                    "BIMAP links the GLB to the audited RFA/RVT by SHA-256. " +
                    "Exporting while the document has unsaved changes would make " +
                    "the viewer geometry differ from the saved audit source.");
                return Result.Cancelled;
            }

            GlbSourceProvenance? provenance =
                TryCreateSourceProvenance(document);

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
                GlbWriter.Write(
                    outputPath,
                    export,
                    provenance);

            Trace.WriteLine(
                FormattableString.Invariant(
                    $"[BIMAP] GLB export completed: elements={export.Elements.Count}, triangles={export.TriangleCount}, vertices={export.VertexCount}, bytes={written.SizeBytes}, sha256={written.Sha256}, sourceFingerprint={(provenance is null ? "unavailable" : "sha256")}"));

            TaskDialog dialog = new(ProductName)
            {
                MainInstruction =
                    "BIMAP viewer model exported successfully.",
                MainContent =
                    $"File:\n{written.Path}\n\n" +
                    $"Elements with geometry: {export.Elements.Count.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Triangles: {export.TriangleCount.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Vertices: {export.VertexCount.ToString(CultureInfo.InvariantCulture)}\n" +
                    $"Size: {FormatBytes(written.SizeBytes)}\n" +
                    (
                        provenance is null
                            ? "Source SHA-256: unavailable\n\n"
                            : $"Source SHA-256: {provenance.Sha256}\n\n"
                    ) +
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

    private static bool HasUnsavedLocalChanges(
        Document document)
    {
        string path =
            document.PathName;

        return
            document.IsModified &&
            !string.IsNullOrWhiteSpace(path) &&
            File.Exists(path);
    }

    private static GlbSourceProvenance?
        TryCreateSourceProvenance(
            Document document)
    {
        string path =
            document.PathName;

        if (string.IsNullOrWhiteSpace(path) ||
            !File.Exists(path))
        {
            /*
             * Unsaved, cloud-hosted, or otherwise non-local documents can still
             * produce a useful GLB, but no source fingerprint is fabricated.
             */
            return null;
        }

        try
        {
            using FileStream stream = new(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete,
                bufferSize: 128 * 1024,
                FileOptions.SequentialScan);

            string sha256 =
                Convert.ToHexString(
                        SHA256.HashData(stream))
                    .ToLowerInvariant();

            return new GlbSourceProvenance(
                FileName:
                    Path.GetFileName(path),
                Sha256:
                    sha256,
                FingerprintKind:
                    "saved-file-sha256",
                DocumentModifiedAtExport:
                    document.IsModified);
        }
        catch (
            Exception exception)
            when (
                exception is IOException
                or UnauthorizedAccessException
                or NotSupportedException
                or ArgumentException)
        {
            Trace.TraceWarning(
                "[BIMAP] Revit source fingerprint unavailable: " +
                $"{exception.GetType().Name}: {exception.Message}");
            return null;
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
