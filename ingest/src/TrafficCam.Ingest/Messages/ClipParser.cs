using System.Diagnostics.CodeAnalysis;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class ClipParser
{
    public const string Schema = "clip/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out Clip? clip,
        [NotNullWhen(false)] out string? error) =>
        ContractJson.TryParse(payload, Schema, record => record.Schema, out clip, out error);
}
