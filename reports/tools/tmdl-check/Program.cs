using Microsoft.AnalysisServices.Tabular;

var folder = args[0];
try
{
    var db = TmdlSerializer.DeserializeDatabaseFromFolder(folder);
    var m = db.Model;
    Console.WriteLine($"OK database compat={db.CompatibilityLevel} tables={m.Tables.Count} relationships={m.Relationships.Count} roles={m.Roles.Count} expressions={m.Expressions.Count}");
    foreach (var t in m.Tables)
    {
        var p = t.Partitions.FirstOrDefault();
        Console.WriteLine($"  {t.Name}: columns={t.Columns.Count(c => c.Type != ColumnType.RowNumber)} measures={t.Measures.Count} mode={p?.Mode} source={(p?.Source as EntityPartitionSource)?.EntityName}");
    }
    foreach (var r in m.Roles)
        Console.WriteLine($"  role {r.Name}: {string.Join("; ", r.TablePermissions.Select(tp => tp.Table.Name + " = " + tp.FilterExpression))}");
}
catch (Exception ex)
{
    Console.WriteLine("FAIL " + ex.GetType().Name + ": " + ex.Message);
    Environment.Exit(1);
}
