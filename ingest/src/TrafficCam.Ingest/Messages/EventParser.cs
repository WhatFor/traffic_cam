using System.Diagnostics.CodeAnalysis;
using System.Text.RegularExpressions;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static partial class EventParser
{
    public const string Schema = "event/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out Event? @event,
        [NotNullWhen(false)] out string? error)
    {
        if (!ContractJson.TryParse(payload, Schema, record => record.Schema, out @event, out error))
            return false;
        // The contract's pattern for a type, which the deserialiser does not check.
        if (!TypePattern().IsMatch(@event.Type))
        {
            error = $"type '{@event.Type}' is not lower-case letters, digits and underscores";
            @event = null;
            return false;
        }
        return true;
    }

    [GeneratedRegex("^[a-z0-9_]+$")]
    private static partial Regex TypePattern();
}
