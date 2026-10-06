using System.Diagnostics.CodeAnalysis;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class ClipDeletedParser
{
    public const string Schema = "clip_deleted/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out ClipDeleted? deleted,
        [NotNullWhen(false)] out string? error) =>
        ContractJson.TryParse(payload, Schema, record => record.Schema, out deleted, out error);
}
