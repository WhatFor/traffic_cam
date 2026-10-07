using System.Globalization;
using System.Text.Json;

namespace TrafficCam.Web;

/// <summary>How the records read on a page.</summary>
public static class Wording
{
    public const string Manual = "manual";

    /// <summary>A trigger type as a heading: red_light becomes "Red light".</summary>
    public static string Title(string type)
    {
        var words = type.Replace('_', ' ');
        return words.Length == 0 ? words : char.ToUpperInvariant(words[0]) + words[1..];
    }

    /// <summary>What is worth saying about a trigger: the reason given for a manual one, else what its event recorded.</summary>
    public static string? Detail(string type, string? reason, string? attrs)
    {
        if (type == Manual || attrs is null)
            return reason;
        using var document = JsonDocument.Parse(attrs);
        var root = document.RootElement;
        string?[] parts =
        [
            root.TryGetProperty("movement", out var movement) && movement.ValueKind == JsonValueKind.String
                ? movement.GetString()
                : null,
            // A near miss is between two movements; an incident candidate lists what was seen of it.
            Listed(root, "movements", " and ", unknown: "unknown"),
            Listed(root, "signs", ", ", unknown: null)?.Replace('_', ' '),
            Measure(root, "pet_s", "{0} s apart"),
            Measure(root, "standing_s", "stood {0} s"),
            Measure(root, "speed_mph", "{0} mph"),
            Measure(root, "time_into_red_s", "{0} s into red"),
            Measure(root, "time_into_amber_s", "{0} s into amber"),
            Measure(root, "stationary_s", "stood {0} s"),
        ];
        var said = string.Join(", ", parts.Where(part => part is not null));
        return said.Length == 0 ? null : said;
    }

    public static string Length(double seconds)
    {
        var whole = (int)Math.Round(seconds);
        string?[] parts =
        [
            whole >= 60 ? string.Create(CultureInfo.InvariantCulture, $"{whole / 60} min") : null,
            whole % 60 != 0 || whole == 0 ? string.Create(CultureInfo.InvariantCulture, $"{whole % 60} s") : null,
        ];
        return string.Join(' ', parts.Where(part => part is not null));
    }

    /// <summary>What a time reads as until the page's script puts it in the reader's own time zone.</summary>
    public static string Utc(DateTimeOffset at) =>
        at.UtcDateTime.ToString("ddd d MMM HH:mm:ss 'UTC'", CultureInfo.InvariantCulture);

    static string? Listed(JsonElement attrs, string name, string separator, string? unknown)
    {
        if (!attrs.TryGetProperty(name, out var list) || list.ValueKind != JsonValueKind.Array)
            return null;
        var items = list.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : unknown)
            .Where(item => item is not null);
        return string.Join(separator, items);
    }

    static string? Measure(JsonElement attrs, string name, string format) =>
        attrs.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            ? string.Format(CultureInfo.InvariantCulture, format, value.GetDouble().ToString("0.#", CultureInfo.InvariantCulture))
            : null;
}
