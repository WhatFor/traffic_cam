namespace TrafficCam.Web;

/// <summary>Which clips the lists are to hold besides the ones still to be looked at.</summary>
public sealed record Listed(bool Archived, bool Viewed);

/// <summary>What a reader has chosen, kept in their browser.</summary>
public static class Preferences
{
    public const string ArchivedCookie = "archived";
    public const string ViewedCookie = "viewed";
    static readonly TimeSpan Kept = TimeSpan.FromDays(365);

    public static Listed Listed(this HttpRequest request) =>
        new(request.Cookies[ArchivedCookie] == "1", request.Cookies[ViewedCookie] == "1");

    public static void Include(this HttpResponse response, string cookie, bool include)
    {
        if (include)
            response.Cookies.Append(
                cookie, "1", new CookieOptions { MaxAge = Kept, HttpOnly = true, SameSite = SameSiteMode.Lax });
        else
            response.Cookies.Delete(cookie);
    }
}
