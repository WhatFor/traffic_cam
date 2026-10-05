// Generates the C# contract types. Run through `just gen-contracts`:
//   dotnet run contracts/gen-csharp.cs -- <schema.json> <output.cs>
#:package NJsonSchema.CodeGeneration.CSharp@11.6.1

using System.Globalization;
using NJsonSchema;
using NJsonSchema.CodeGeneration;
using NJsonSchema.CodeGeneration.CSharp;

var schema = await JsonSchema.FromFileAsync(args[0]);

var settings = new CSharpGeneratorSettings
{
    Namespace = "TrafficCam.Contracts",
    JsonLibrary = CSharpJsonLibrary.SystemTextJson,
    // 9.0 and later emit [JsonStringEnumMemberName], which keeps the wire values of enums.
    JsonLibraryVersion = 10.0m,
    ClassStyle = CSharpClassStyle.Poco,
    UseRequiredKeyword = true,
    GenerateNullableReferenceTypes = true,
    GenerateDataAnnotations = false,
    DateTimeType = "System.DateTimeOffset",
    PropertyNameGenerator = new PascalCase(),
    EnumNameGenerator = new PascalCase(),
};

// The root schema only holds definitions, so each one is registered explicitly.
var resolver = new CSharpTypeResolver(settings);
resolver.RegisterSchemaDefinitions(schema.Definitions);

File.WriteAllText(args[1], new CSharpGenerator(schema, settings, resolver).GenerateFile());

// snake_case -> PascalCase for property and enum member names.
class PascalCase : IPropertyNameGenerator, IEnumNameGenerator
{
    public string Generate(JsonSchemaProperty property) => Convert(property.Name);

    public string Generate(int index, string? name, object? value, JsonSchema schema) => Convert(name!);

    static string Convert(string name) =>
        string.Concat(name.Split('_').Select(part => CultureInfo.InvariantCulture.TextInfo.ToTitleCase(part)));
}
