using System.Diagnostics.CodeAnalysis;
using System.Text.Json;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class PassageParser
{
    public const string Schema = "passage/1";

    // The generated type marks required members; this also rejects null where it is not allowed.
    static readonly JsonSerializerOptions Options = new() { RespectNullableAnnotations = true };

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out Passage? passage,
        [NotNullWhen(false)] out string? error)
    {
        passage = null;
        try
        {
            passage = JsonSerializer.Deserialize<Passage>(payload, Options);
        }
        catch (JsonException exception)
        {
            error = exception.Message;
            return false;
        }

        if (passage is null)
        {
            error = "the payload is null";
            return false;
        }
        if (passage.Schema != Schema)
        {
            error = $"schema is '{passage.Schema}', expected '{Schema}'";
            passage = null;
            return false;
        }
        error = null;
        return true;
    }
}
