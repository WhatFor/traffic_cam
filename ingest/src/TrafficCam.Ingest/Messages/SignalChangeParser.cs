using System.Diagnostics.CodeAnalysis;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class SignalChangeParser
{
    public const string Schema = "signal_change/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out SignalChange? change,
        [NotNullWhen(false)] out string? error) =>
        ContractJson.TryParse(payload, Schema, record => record.Schema, out change, out error);
}
