using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Autodesk.Oss;
using Autodesk.Oss.Model;
using Autodesk.SDKManager;

namespace Bimap.RevitWorker.Broker;

internal static class Program
{
    private const int ExitOk = 0;
    private const int ExitCli = 2;
    private const int ExitConfiguration = 3;
    private const int ExitRemoteService = 4;
    private const int ExitOutputValidation = 5;
    private const int ExitUnexpected = 10;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            var request = WorkerRequest.Parse(args);
            var settings = BrokerSettings.FromEnvironment();
            using var cts = new CancellationTokenSource(settings.OperationTimeout);
            await RunAsync(request, settings, cts.Token).ConfigureAwait(false);
            return ExitOk;
        }
        catch (CliException ex)
        {
            WriteError("cli", ex.Message);
            return ExitCli;
        }
        catch (ConfigurationException ex)
        {
            WriteError("configuration", ex.Message);
            return ExitConfiguration;
        }
        catch (RemoteServiceException ex)
        {
            WriteError("aps", ex.Message);
            return ExitRemoteService;
        }
        catch (OutputValidationException ex)
        {
            WriteError("output", ex.Message);
            return ExitOutputValidation;
        }
        catch (OperationCanceledException)
        {
            WriteError("timeout", "The native Revit operation exceeded the configured timeout.");
            return ExitRemoteService;
        }
        catch (Exception ex)
        {
            WriteError("unexpected", $"{ex.GetType().Name}: {Sanitize(ex.Message)}");
            return ExitUnexpected;
        }
    }

    private static async Task RunAsync(
        WorkerRequest request,
        BrokerSettings settings,
        CancellationToken cancellationToken)
    {
        var sourceInfo = new FileInfo(request.InputPath);
        if (sourceInfo.Length > settings.MaxSourceBytes)
        {
            throw new CliException("The Revit source exceeds the configured native-worker source-size limit.");
        }

        var token = await ApsAuthentication.GetTokenAsync(
            settings.ClientId,
            settings.ClientSecret,
            cancellationToken).ConfigureAwait(false);

        var authenticationProvider = new StaticAuthenticationProvider(token);
        var oss = new OssClient(authenticationProvider: authenticationProvider);
        await EnsureBucketAsync(oss, settings.BucketKey).ConfigureAwait(false);

        var operationId = Guid.NewGuid().ToString("N");
        var sourceExtension = request.SourceFormat;
        var inputObjectKey = $"bimap-revit/{operationId}/input.{sourceExtension}";
        var outputObjectKey = $"bimap-revit/{operationId}/result.bin";

        string? inputObjectId = null;
        string? outputObjectId = null;
        string? outputTempPath = null;
        string? stagedOutputPath = null;
        string? workItemId = null;
        var workItemCompleted = false;

        try
        {
            var inputDetails = await oss.UploadObjectAsync(
                settings.BucketKey,
                inputObjectKey,
                request.InputPath,
                cancellationToken).ConfigureAwait(false);
            inputObjectId = RequireObjectId(inputDetails, "input");

            outputTempPath = Path.Combine(Path.GetTempPath(), $"bimap-revit-{operationId}-placeholder.bin");
            await File.WriteAllBytesAsync(outputTempPath, new byte[] { 0x00 }, cancellationToken).ConfigureAwait(false);
            var outputDetails = await oss.UploadObjectAsync(
                settings.BucketKey,
                outputObjectKey,
                outputTempPath,
                cancellationToken).ConfigureAwait(false);
            outputObjectId = RequireObjectId(outputDetails, "output");

            var parameters = request.ToAppBundleParameters();
            var parameterJson = JsonSerializer.Serialize(parameters, JsonOptions);
            var dataUri = "data:application/json," + Uri.EscapeDataString(parameterJson);

            workItemId = await SubmitWorkItemAsync(
                settings,
                token,
                inputObjectId,
                outputObjectId,
                dataUri,
                cancellationToken).ConfigureAwait(false);

            await WaitForWorkItemAsync(
                settings,
                token,
                workItemId,
                cancellationToken).ConfigureAwait(false);
            workItemCompleted = true;

            Directory.CreateDirectory(request.OutputDirectory);
            stagedOutputPath = Path.Combine(
                request.OutputDirectory,
                $".{Path.GetFileName(request.OutputPath)}.{operationId}.tmp");

            await oss.DownloadObjectAsync(
                settings.BucketKey,
                outputObjectKey,
                stagedOutputPath,
                cancellationToken).ConfigureAwait(false);

            await ValidateOutputAsync(request, stagedOutputPath, settings, cancellationToken).ConfigureAwait(false);
            AtomicReplace(stagedOutputPath, request.OutputPath);
            stagedOutputPath = null;
        }
        catch
        {
            if (!workItemCompleted && !string.IsNullOrWhiteSpace(workItemId))
            {
                await TryCancelWorkItemAsync(settings, token, workItemId).ConfigureAwait(false);
            }
            throw;
        }
        finally
        {
            if (outputTempPath is not null)
            {
                TryDeleteLocal(outputTempPath);
            }
            if (stagedOutputPath is not null)
            {
                TryDeleteLocal(stagedOutputPath);
            }

            await TryDeleteObjectAsync(oss, settings.BucketKey, inputObjectKey).ConfigureAwait(false);
            await TryDeleteObjectAsync(oss, settings.BucketKey, outputObjectKey).ConfigureAwait(false);
        }
    }

    private static string RequireObjectId(ObjectDetails details, string role)
    {
        var objectId = details?.ObjectId;
        if (string.IsNullOrWhiteSpace(objectId))
        {
            throw new RemoteServiceException($"APS OSS did not return an object identifier for the {role} object.");
        }
        return objectId;
    }

    private static async Task EnsureBucketAsync(OssClient oss, string bucketKey)
    {
        try
        {
            await oss.GetBucketDetailsAsync(bucketKey).ConfigureAwait(false);
            return;
        }
        catch
        {
            // The follow-up create/get sequence distinguishes an absent bucket
            // from a persistent credentials/region failure without depending on
            // SDK-internal exception types.
        }

        try
        {
            await oss.CreateBucketAsync(
                Region.US,
                new CreateBucketsPayload
                {
                    BucketKey = bucketKey,
                    PolicyKey = PolicyKey.Temporary,
                }).ConfigureAwait(false);
        }
        catch
        {
            try
            {
                await oss.GetBucketDetailsAsync(bucketKey).ConfigureAwait(false);
                return;
            }
            catch (Exception ex)
            {
                throw new RemoteServiceException(
                    "APS OSS bucket access could not be established.",
                    ex);
            }
        }
    }

    private static async Task<string> SubmitWorkItemAsync(
        BrokerSettings settings,
        string token,
        string inputObjectId,
        string outputObjectId,
        string parameterDataUri,
        CancellationToken cancellationToken)
    {
        using var client = CreateDaClient(settings, token);
        var payload = new
        {
            activityId = settings.ActivityId,
            arguments = new Dictionary<string, object>
            {
                ["inputFile"] = new
                {
                    url = inputObjectId,
                    headers = new Dictionary<string, string>
                    {
                        ["Authorization"] = $"Bearer {token}",
                    },
                },
                ["inputJson"] = new
                {
                    url = parameterDataUri,
                },
                ["outputFile"] = new
                {
                    url = outputObjectId,
                    verb = "put",
                    headers = new Dictionary<string, string>
                    {
                        ["Authorization"] = $"Bearer {token}",
                        ["Content-Type"] = "application/octet-stream",
                    },
                },
            },
        };

        using var content = new StringContent(
            JsonSerializer.Serialize(payload, JsonOptions),
            Encoding.UTF8,
            "application/json");
        using var response = await client.PostAsync("v3/workitems", content, cancellationToken).ConfigureAwait(false);
        var responseBody = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new RemoteServiceException(
                $"APS Automation rejected the work item with HTTP {(int)response.StatusCode}.");
        }

        using var document = JsonDocument.Parse(responseBody);
        if (!document.RootElement.TryGetProperty("id", out var idNode))
        {
            throw new RemoteServiceException("APS Automation work-item response did not contain an id.");
        }

        var id = idNode.GetString();
        if (string.IsNullOrWhiteSpace(id))
        {
            throw new RemoteServiceException("APS Automation returned an empty work-item id.");
        }
        return id;
    }

    private static async Task WaitForWorkItemAsync(
        BrokerSettings settings,
        string token,
        string workItemId,
        CancellationToken cancellationToken)
    {
        using var client = CreateDaClient(settings, token);
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            using var response = await client.GetAsync(
                $"v3/workitems/{Uri.EscapeDataString(workItemId)}",
                cancellationToken).ConfigureAwait(false);
            var responseBody = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                throw new RemoteServiceException(
                    $"APS Automation status lookup failed with HTTP {(int)response.StatusCode}.");
            }

            using var document = JsonDocument.Parse(responseBody);
            var status = document.RootElement.TryGetProperty("status", out var statusNode)
                ? statusNode.GetString()?.Trim()
                : null;

            if (string.Equals(status, "success", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            if (!string.IsNullOrWhiteSpace(status)
                && !string.Equals(status, "pending", StringComparison.OrdinalIgnoreCase)
                && !string.Equals(status, "inprogress", StringComparison.OrdinalIgnoreCase))
            {
                throw new RemoteServiceException(
                    $"APS Automation work item finished with status '{Sanitize(status)}'.");
            }

            await Task.Delay(settings.PollInterval, cancellationToken).ConfigureAwait(false);
        }
    }

    private static async Task TryCancelWorkItemAsync(
        BrokerSettings settings,
        string token,
        string workItemId)
    {
        try
        {
            using var client = CreateDaClient(settings, token);
            using var response = await client.DeleteAsync(
                $"v3/workitems/{Uri.EscapeDataString(workItemId)}").ConfigureAwait(false);
        }
        catch
        {
            // Best-effort remote cancellation. The primary failure remains authoritative.
        }
    }

    private static HttpClient CreateDaClient(BrokerSettings settings, string token)
    {
        var client = new HttpClient
        {
            BaseAddress = settings.DesignAutomationBaseUri,
            Timeout = Timeout.InfiniteTimeSpan,
        };
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        return client;
    }

    private static async Task ValidateOutputAsync(
        WorkerRequest request,
        string path,
        BrokerSettings settings,
        CancellationToken cancellationToken)
    {
        var info = new FileInfo(path);
        if (!info.Exists || info.Length <= 0)
        {
            throw new OutputValidationException("The native Revit worker returned an empty output artifact.");
        }
        if (info.Length > settings.MaxOutputBytes)
        {
            throw new OutputValidationException("The native Revit worker output exceeds the configured size limit.");
        }

        await using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        switch (request.Mode)
        {
            case WorkerMode.Inspect:
            case WorkerMode.Extract:
                using (var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken).ConfigureAwait(false))
                {
                    if (document.RootElement.ValueKind != JsonValueKind.Object)
                    {
                        throw new OutputValidationException("The Revit JSON output root must be an object.");
                    }
                }
                break;

            case WorkerMode.Convert:
                var header = new byte[12];
                if (await stream.ReadAsync(header, cancellationToken).ConfigureAwait(false) != header.Length
                    || header[0] != (byte)'g'
                    || header[1] != (byte)'l'
                    || header[2] != (byte)'T'
                    || header[3] != (byte)'F'
                    || BitConverter.ToUInt32(header, 4) != 2U)
                {
                    throw new OutputValidationException("The Revit conversion output is not a valid glTF 2.0 GLB container.");
                }
                var declaredLength = BitConverter.ToUInt32(header, 8);
                if (declaredLength != info.Length)
                {
                    throw new OutputValidationException("The GLB declared length does not match the downloaded artifact length.");
                }
                break;

            case WorkerMode.Preview:
                var png = new byte[8];
                if (await stream.ReadAsync(png, cancellationToken).ConfigureAwait(false) != png.Length
                    || !png.SequenceEqual(new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A }))
                {
                    throw new OutputValidationException("The Revit preview output is not a PNG image.");
                }
                break;
        }
    }

    private static void AtomicReplace(string stagedPath, string outputPath)
    {
        var destination = Path.GetFullPath(outputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        if (File.Exists(destination))
        {
            File.Delete(destination);
        }
        File.Move(stagedPath, destination);
    }

    private static async Task TryDeleteObjectAsync(OssClient oss, string bucketKey, string objectKey)
    {
        try
        {
            await oss.DeleteObjectAsync(bucketKey, objectKey).ConfigureAwait(false);
        }
        catch
        {
            // Best-effort cleanup. The bucket uses APS temporary retention and
            // cleanup failure must not alter an otherwise valid BIMAP result.
        }
    }

    private static void TryDeleteLocal(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
            // Best effort only.
        }
    }

    private static void WriteError(string category, string message)
    {
        Console.Error.WriteLine($"BIMAP_REVIT_WORKER_ERROR[{category}]: {Sanitize(message)}");
    }

    private static string Sanitize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "unspecified error";
        }
        return value.Replace('\r', ' ').Replace('\n', ' ').Trim()[..Math.Min(value.Replace('\r', ' ').Replace('\n', ' ').Trim().Length, 500)];
    }
}

internal enum WorkerMode
{
    Inspect,
    Extract,
    Convert,
    Preview,
}

internal sealed record WorkerRequest(
    WorkerMode Mode,
    string InputPath,
    string SourceFormat,
    string OutputPath,
    IReadOnlyList<string> Datasets,
    string? TargetFormat,
    string? OutputStem)
{
    internal string OutputDirectory => Path.GetDirectoryName(OutputPath)!;

    internal static WorkerRequest Parse(string[] args)
    {
        var options = ParseOptions(args);
        var modeText = Required(options, "mode").ToLowerInvariant();
        var mode = modeText switch
        {
            "inspect" => WorkerMode.Inspect,
            "extract" => WorkerMode.Extract,
            "convert" => WorkerMode.Convert,
            "preview" => WorkerMode.Preview,
            _ => throw new CliException("--mode must be one of inspect, extract, convert, preview."),
        };

        var input = Path.GetFullPath(Required(options, "input"));
        if (!Path.IsPathFullyQualified(input) || !File.Exists(input) || new FileInfo(input).Length <= 0)
        {
            throw new CliException("--input must identify an existing non-empty absolute RFA/RVT file.");
        }

        var sourceFormat = Required(options, "source-format").Trim().ToLowerInvariant();
        if (sourceFormat is not ("rfa" or "rvt"))
        {
            throw new CliException("--source-format must be rfa or rvt.");
        }
        if (!string.Equals(Path.GetExtension(input), $".{sourceFormat}", StringComparison.OrdinalIgnoreCase))
        {
            throw new CliException("--source-format does not match the source file extension.");
        }

        var output = Path.GetFullPath(Required(options, "output"));
        if (!Path.IsPathFullyQualified(output) || string.Equals(input, output, StringComparison.OrdinalIgnoreCase))
        {
            throw new CliException("--output must be a different absolute path from --input.");
        }

        var datasets = options.TryGetValue("datasets", out var datasetsText)
            ? datasetsText.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Select(value => value.ToLowerInvariant())
                .Distinct(StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        var allowedDatasets = new HashSet<string>(StringComparer.Ordinal)
        {
            "elements", "properties", "quantities", "materials",
        };
        if (datasets.Any(value => !allowedDatasets.Contains(value)))
        {
            throw new CliException("--datasets contains an unsupported dataset name.");
        }

        string? targetFormat = null;
        string? outputStem = null;
        if (mode is WorkerMode.Convert)
        {
            targetFormat = Required(options, "target-format").Trim().ToLowerInvariant();
            if (targetFormat != "glb")
            {
                throw new CliException("--target-format must be glb for Revit conversion.");
            }
            outputStem = Required(options, "output-stem").Trim();
            if (string.IsNullOrWhiteSpace(outputStem) || outputStem.Length > 120)
            {
                throw new CliException("--output-stem must be a non-empty value of at most 120 characters.");
            }
        }

        return new WorkerRequest(mode, input, sourceFormat, output, datasets, targetFormat, outputStem);
    }

    internal object ToAppBundleParameters() => new
    {
        mode = Mode.ToString().ToLowerInvariant(),
        source_format = SourceFormat,
        datasets = Datasets,
        target_format = TargetFormat,
        output_stem = OutputStem,
        output_file = "result.bin",
        schema_version = "1.0.0",
    };

    private static Dictionary<string, string> ParseOptions(string[] args)
    {
        if (args.Length == 0)
        {
            throw new CliException("No worker arguments were supplied.");
        }

        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var index = 0; index < args.Length; index += 2)
        {
            var key = args[index];
            if (!key.StartsWith("--", StringComparison.Ordinal) || index + 1 >= args.Length)
            {
                throw new CliException("Arguments must use '--name value' pairs.");
            }
            key = key[2..].Trim();
            if (string.IsNullOrWhiteSpace(key) || result.ContainsKey(key))
            {
                throw new CliException("Worker arguments contain an empty or duplicate option name.");
            }
            result[key] = args[index + 1];
        }
        return result;
    }

    private static string Required(IReadOnlyDictionary<string, string> options, string name)
    {
        if (!options.TryGetValue(name, out var value) || string.IsNullOrWhiteSpace(value))
        {
            throw new CliException($"--{name} is required.");
        }
        return value.Trim();
    }
}

internal sealed record BrokerSettings(
    string ClientId,
    string ClientSecret,
    string ActivityId,
    string BucketKey,
    Uri DesignAutomationBaseUri,
    TimeSpan OperationTimeout,
    TimeSpan PollInterval,
    long MaxSourceBytes,
    long MaxOutputBytes)
{
    internal static BrokerSettings FromEnvironment()
    {
        var clientId = RequireEnvironment("APS_CLIENT_ID");
        var clientSecret = RequireEnvironment("APS_CLIENT_SECRET");
        var activityId = RequireEnvironment("BIMAP_APS_DA_ACTIVITY_ID");
        var bucket = Environment.GetEnvironmentVariable("BIMAP_APS_OSS_BUCKET_KEY")?.Trim();
        if (string.IsNullOrWhiteSpace(bucket))
        {
            bucket = BuildDefaultBucketKey(clientId);
        }
        ValidateBucketKey(bucket);

        var baseUrl = Environment.GetEnvironmentVariable("BIMAP_APS_DA_BASE_URL")?.Trim();
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            baseUrl = "https://developer.api.autodesk.com/da/us-east/";
        }
        if (!baseUrl.EndsWith("/", StringComparison.Ordinal))
        {
            baseUrl += "/";
        }
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var baseUri) || baseUri.Scheme != Uri.UriSchemeHttps)
        {
            throw new ConfigurationException("BIMAP_APS_DA_BASE_URL must be an absolute HTTPS URI.");
        }

        var timeoutSeconds = PositiveInt("BIMAP_APS_DA_TIMEOUT_SECONDS", 600, 30, 3600);
        var pollSeconds = PositiveInt("BIMAP_APS_DA_POLL_SECONDS", 3, 1, 60);
        var maxSourceBytes = PositiveLong("BIMAP_REVIT_WORKER_MAX_SOURCE_BYTES", 512L * 1024L * 1024L, 1, 4L * 1024L * 1024L * 1024L);
        var maxOutputBytes = PositiveLong("BIMAP_REVIT_WORKER_MAX_OUTPUT_BYTES", 256L * 1024L * 1024L, 1, 2L * 1024L * 1024L * 1024L);

        return new BrokerSettings(
            clientId,
            clientSecret,
            activityId,
            bucket,
            baseUri,
            TimeSpan.FromSeconds(timeoutSeconds),
            TimeSpan.FromSeconds(pollSeconds),
            maxSourceBytes,
            maxOutputBytes);
    }

    private static string RequireEnvironment(string name)
    {
        var value = Environment.GetEnvironmentVariable(name)?.Trim();
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ConfigurationException($"Required environment variable {name} is not configured.");
        }
        return value;
    }

    private static int PositiveInt(string name, int fallback, int minimum, int maximum)
    {
        var raw = Environment.GetEnvironmentVariable(name)?.Trim();
        if (string.IsNullOrWhiteSpace(raw))
        {
            return fallback;
        }
        if (!int.TryParse(raw, out var value) || value < minimum || value > maximum)
        {
            throw new ConfigurationException($"{name} must be an integer between {minimum} and {maximum}.");
        }
        return value;
    }

    private static long PositiveLong(string name, long fallback, long minimum, long maximum)
    {
        var raw = Environment.GetEnvironmentVariable(name)?.Trim();
        if (string.IsNullOrWhiteSpace(raw))
        {
            return fallback;
        }
        if (!long.TryParse(raw, out var value) || value < minimum || value > maximum)
        {
            throw new ConfigurationException($"{name} must be an integer between {minimum} and {maximum}.");
        }
        return value;
    }

    private static string BuildDefaultBucketKey(string clientId)
    {
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(clientId))).ToLowerInvariant()[..20];
        return $"bimap-revit-{digest}";
    }

    private static void ValidateBucketKey(string value)
    {
        if (value.Length is < 3 or > 128
            || value.Any(ch => !(char.IsLower(ch) || char.IsDigit(ch) || ch is '-' or '_' or '.')))
        {
            throw new ConfigurationException(
                "BIMAP_APS_OSS_BUCKET_KEY must be 3-128 characters using lowercase letters, digits, '-', '_' or '.'.");
        }
    }
}

internal static class ApsAuthentication
{
    internal static async Task<string> GetTokenAsync(
        string clientId,
        string clientSecret,
        CancellationToken cancellationToken)
    {
        using var client = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        var basic = Convert.ToBase64String(Encoding.UTF8.GetBytes($"{clientId}:{clientSecret}"));
        using var request = new HttpRequestMessage(
            HttpMethod.Post,
            "https://developer.api.autodesk.com/authentication/v2/token");
        request.Headers.Authorization = new AuthenticationHeaderValue("Basic", basic);
        request.Content = new FormUrlEncodedContent(new Dictionary<string, string>
        {
            ["grant_type"] = "client_credentials",
            ["scope"] = "data:read data:write bucket:create bucket:read bucket:update code:all",
        });

        using var response = await client.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new RemoteServiceException(
                $"APS OAuth token request failed with HTTP {(int)response.StatusCode}.");
        }

        using var document = JsonDocument.Parse(body);
        if (!document.RootElement.TryGetProperty("access_token", out var tokenNode))
        {
            throw new RemoteServiceException("APS OAuth response did not include access_token.");
        }
        var token = tokenNode.GetString();
        if (string.IsNullOrWhiteSpace(token))
        {
            throw new RemoteServiceException("APS OAuth returned an empty access token.");
        }
        return token;
    }
}

internal sealed class CliException : Exception
{
    internal CliException(string message) : base(message) { }
}

internal sealed class ConfigurationException : Exception
{
    internal ConfigurationException(string message) : base(message) { }
}

internal sealed class RemoteServiceException : Exception
{
    internal RemoteServiceException(string message) : base(message) { }
    internal RemoteServiceException(string message, Exception inner) : base(message, inner) { }
}

internal sealed class OutputValidationException : Exception
{
    internal OutputValidationException(string message) : base(message) { }
}
