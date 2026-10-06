using System.Diagnostics.CodeAnalysis;
using System.Text.Json;

namespace TrafficCam.Ingest.Messages;

static class ContractJson
{
    // The generated types mark required members; this also rejects null where it is not allowed.
    static readonly JsonSerializerOptions Options = new() { RespectNullableAnnotations = true };

    /// <summary>Reads a payload as <typeparamref name="T"/> and checks it names the expected schema.</summary>
    public static bool TryParse<T>(
        ReadOnlySpan<byte> payload,
        string schema,
        Func<T, string> schemaOf,
        [NotNullWhen(true)] out T? record,
        [NotNullWhen(false)] out string? error)
        where T : class
    {
        record = null;
        try
        {
            record = JsonSerializer.Deserialize<T>(payload, Options);
        }
        catch (JsonException exception)
        {
            error = exception.Message;
            return false;
        }

        if (record is null)
        {
            error = "the payload is null";
            return false;
        }
        if (schemaOf(record) != schema)
        {
            error = $"schema is '{schemaOf(record)}', expected '{schema}'";
            record = null;
            return false;
        }
        error = null;
        return true;
    }
}
