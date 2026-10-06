using System.Diagnostics.CodeAnalysis;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class PassageParser
{
    public const string Schema = "passage/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out Passage? passage,
        [NotNullWhen(false)] out string? error) =>
        ContractJson.TryParse(payload, Schema, record => record.Schema, out passage, out error);
}
