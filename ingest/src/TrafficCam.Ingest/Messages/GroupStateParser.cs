using System.Diagnostics.CodeAnalysis;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Messages;

public static class GroupStateParser
{
    public const string Schema = "group_state/1";

    public static bool TryParse(
        ReadOnlySpan<byte> payload,
        [NotNullWhen(true)] out GroupState? state,
        [NotNullWhen(false)] out string? error) =>
        ContractJson.TryParse(payload, Schema, record => record.Schema, out state, out error);
}
