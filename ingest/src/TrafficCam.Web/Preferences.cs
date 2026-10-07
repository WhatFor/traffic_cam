namespace TrafficCam.Web;

/// <summary>What a reader has chosen, kept in their browser.</summary>
public static class Preferences
{
    public const string ArchivedCookie = "archived";
    static readonly TimeSpan Kept = TimeSpan.FromDays(365);

    public static bool IncludeArchived(this HttpRequest request) => request.Cookies[ArchivedCookie] == "1";

    public static void IncludeArchived(this HttpResponse response, bool include)
    {
        if (include)
            response.Cookies.Append(
                ArchivedCookie, "1", new CookieOptions { MaxAge = Kept, HttpOnly = true, SameSite = SameSiteMode.Lax });
        else
            response.Cookies.Delete(ArchivedCookie);
    }
}
