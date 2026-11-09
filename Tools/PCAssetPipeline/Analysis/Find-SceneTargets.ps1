[CmdletBinding()]
param([string]$AnalysisRoot=(Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) 'Saved/PCAssetAnalysis'))
$ErrorActionPreference = 'Stop'
$indexRoot = Join-Path $AnalysisRoot 'Indexing'
$maps = Get-Content -LiteralPath (Join-Path $indexRoot 'environment_map_locators.json') -Raw | ConvertFrom-Json
$cache = @{}

function Build-MapTarget($relative, $purpose, $basis) {
    $entryPath = '../../../NZM/Content/' + $relative + '.umap'
    $locs = @($maps | Where-Object { $_.entry.path -ceq $entryPath })
    if ($locs.Count -eq 0) { throw ('Missing target ' + $entryPath) }
    $versions = @(foreach ($loc in $locs) {
        $indexPath = Join-Path $indexRoot ($loc.archive.Replace('/', '__') + '.json')
        if (-not $cache.ContainsKey($indexPath)) {
            $cache[$indexPath] = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
        }
        $sidecar = [IO.Path]::ChangeExtension($entryPath, '.uexp')
        $bulk = [IO.Path]::ChangeExtension($entryPath, '.ubulk')
        [ordered]@{
            archive = $loc.archive
            index_json = $indexPath
            is_update_archive = $loc.archive.StartsWith('Saved/')
            mount_priority_verified = $false
            selected = $false
            entries = @($cache[$indexPath].entries | Where-Object {
                $_.path -ceq $entryPath -or $_.path -ceq $sidecar -or $_.path -ceq $bulk
            })
        }
    })
    [ordered]@{
        virtual_package = '/Game/' + $relative
        cooked_package = 'NZM/Content/' + $relative
        purpose = $purpose
        supporting_basis = $basis
        verification_status = 'locator_candidate_only'
        versions = $versions
    }
}

$sceneSpecs = @(
    @{
        id = 'standard_scene103'
        basis = 'Existing viewer analysis associates standard103 with Light_Role and General_Background. This report confirms PC index identity, not runtime Scene103 routing.'
        primary = @(
            @('Maps/Lobby/Role/Lobby_Scene_Light_Role', 'lights / SkyLight / postprocess candidate', 'Exact standard role map; base and seasonal versions retained.'),
            @('Maps/Lobby/Role/Lobby_Scene_General_Background_HD', 'explicit HD background comparison', 'Exact _HD suffix; platform selection and quality are unverified.'),
            @('Maps/Lobby/Role/Lobby_Scene_General_Background', 'standard background comparison', 'Exact historical standard background leaf; base and seasonal versions retained.')
        )
        follow = @(
            ,@('Maps/Lobby/Role/Lobby_Scene_Camera_Role', 'camera/reference follow-up', 'Role camera map; effective camera requires deserialization.')
        )
    },
    @{
        id = 'queen'
        basis = 'Role_Queen contains Role and Lobby_Preview branches. Their current outfit routes are unresolved.'
        primary = @(
            @('Maps/Lobby_New/Role_Queen/Lobby_Scene_Light_Role_Queen', 'role lighting candidate', 'Role_Queen directory and exact Light_Role leaf.'),
            @('Maps/Lobby_New/Role_Queen/Lobby_Scene_Light_Lobby_Queen_Preview', 'preview lighting candidate', 'Distinct Preview map; base and two seasonal versions retained.')
        )
        follow = @(
            @('Maps/Lobby_New/Role_Queen/Lobby_Scene_Mesh_Role_Queen', 'role geometry/material dependencies', 'Same Role branch as the role light map.'),
            @('Maps/Lobby_New/Role_Queen/Lobby_Scene_Mesh_Lobby_Queen_Preview', 'preview geometry/material dependencies', 'Same Preview branch as the preview light map.')
        )
    },
    @{
        id = 'lucia'
        basis = 'Role_AngelLucia has a role light map and mesh sublevel. Lobby_AngelLucia is a separate lobby/effects alternative.'
        primary = @(
            ,@('Maps/Lobby_New/Role_AngelLucia/Lobby_Scene_Light_Role_Role_AngelLucia', 'role lighting candidate', 'Exact repeated Role_Role spelling retained.')
        )
        follow = @(
            @('Maps/Lobby_New/Role_AngelLucia/Role_AngelLucia_Main/Lobby_Scene_Mesh_Role_AngelLucia', 'role geometry/material dependencies', 'Same Role_AngelLucia_Main branch.'),
            @('Maps/Lobby_New/Lobby_AngelLucia/Lobby_Scene_Effect_Lobby_AngelLucia', 'lobby particle references', 'Effect-labelled lobby map; effect appearance unverified.'),
            @('Maps/Lobby_New/Lobby_AngelLucia/Lobby_Scene_Light_Lobby_AngelLucia', 'lobby light alternative', 'Lobby branch not assumed to be role-page lighting.'),
            @('Maps/Lobby_New/Lobby_AngelLucia/Lobby_Scene_Mesh_Lobby_AngelLucia', 'lobby geometry alternative', 'Lobby branch not assumed to be role-page geometry.')
        )
    },
    @{
        id = 'naimei'
        basis = 'Role_Nai_Mei and Role_Naimei are separate directories. Outfit and runtime selection are unresolved.'
        primary = @(
            @('Maps/Lobby_New/Role_Nai_Mei/Lobby_Scene_Light_Role_Nai_Mei', 'first role lighting candidate', 'Exact Role_Nai_Mei spelling; base and two seasonal versions retained.'),
            @('Maps/Lobby_New/Role_Naimei/Lobby_Scene_Light_Role_Naimei', 'second role branch comparison', 'Different Role_Naimei directory; no quality or outfit identity inferred.')
        )
        follow = @(
            @('Maps/Lobby_New/Role_Nai_Mei/Lobby_Scene_Mesh_Role_Nai_Mei', 'first role geometry/material dependencies', 'Same Role_Nai_Mei branch.'),
            @('Maps/Lobby_New/Role_Naimei/Lobby_Scene_Mesh_Role_Naimei', 'second role geometry/material dependencies', 'Same Role_Naimei branch.'),
            @('Maps/Lobby_New/Role_Naimei/Lobby_Scene_Light_Role_NaiMei_01', 'alternate light inspection', 'Distinct _01 package; runtime selection unverified.'),
            @('Maps/Lobby_New/Role_Naimei/Lobby_Scene_Mesh_Role_NaiMei_01', 'alternate geometry inspection', 'Distinct _01 package; runtime selection unverified.')
        )
    },
    @{
        id = 'bianca'
        basis = 'No dedicated Role_Bianca map in supplied locators. Combined Role_NaimeiBianca/EditorOnly is a role-labelled candidate; the EditorOnly path is not proof of a runtime-usable scene.'
        primary = @(
            ,@('Maps/Lobby_New/Role_NaimeiBianca/EditorOnly/Lobby_Scene_Light_Role_NaimeiBianca', 'role-labelled light needing routing verification', 'Only combined role light located; EditorOnly is preserved verbatim.')
        )
        follow = @(
            @('Maps/Lobby_New/Role_NaimeiBianca/EditorOnly/Lobby_Scene_Mesh_Role_NaimeiBianca', 'role-labelled geometry/material dependencies', 'Same EditorOnly role branch.'),
            @('Maps/Lobby_New/Lobby_NaimeiBianca/Lobby_Scene_Light_Lobby_NaimeiBianca', 'combined lobby light alternative', 'Lobby branch not established as Bianca role page.'),
            @('Maps/Lobby_New/Lobby_NaimeiBianca/Lobby_Scene_Mesh_Lobby_NaimeiBianca', 'combined lobby geometry alternative', 'Lobby branch not established as Bianca role page.'),
            @('Maps/Lobby_New/Lobby_NaimeiBianca/Lobby_Scene_Effect_Lobby_NaimeiBianca', 'combined lobby particle references', 'Effect-labelled lobby package; appearance unverified.')
        )
    }
)
$scenes = @(foreach ($spec in $sceneSpecs) {
    [ordered]@{
        id = $spec.id
        relationship_basis = $spec.basis
        pc_hd_branch_confirmed = $false
        runtime_route_verified = $false
        minimum_parameter_targets = @(foreach ($row in $spec.primary) { Build-MapTarget $row[0] $row[1] $row[2] })
        follow_up_targets = @(foreach ($row in $spec.follow) { Build-MapTarget $row[0] $row[1] $row[2] })
    }
})

$tableNames = @('PostProcessDOFTable', 'PostProcessEffect', 'PostProcessRadialBlurTable', 'NZGameClockConfigTable', 'SeasonCollectionAvatarSceneConf')
$tableEntries = [Collections.Generic.List[object]]::new()
$allIndexFiles = @(Get-ChildItem -LiteralPath $indexRoot -Filter '*.pak.json')
foreach ($file in $allIndexFiles) {
    $raw = [IO.File]::ReadAllText($file.FullName)
    if ($raw -notmatch 'PostProcessDOFTable|PostProcessEffect|PostProcessRadialBlurTable|NZGameClockConfigTable|SeasonCollectionAvatarSceneConf') { continue }
    $data = $raw | ConvertFrom-Json
    foreach ($entry in $data.entries) {
        if ([IO.Path]::GetFileNameWithoutExtension($entry.path) -in $tableNames) {
            $tableEntries.Add([pscustomobject]@{ archive = $data.archive; index_json = $file.FullName; entry = $entry })
        }
    }
}
$tables = @(foreach ($name in $tableNames) {
    $entries = @($tableEntries | Where-Object { [IO.Path]::GetFileNameWithoutExtension($_.entry.path) -ceq $name })
    $packagePaths = @($entries | Where-Object { $_.entry.path.EndsWith('.uasset') } | ForEach-Object { $_.entry.path.Substring(0, $_.entry.path.Length - 7) } | Sort-Object -Unique)
    foreach ($packagePath in $packagePaths) {
        $versions = @($entries | Where-Object { $_.entry.path.StartsWith($packagePath + '.', [StringComparison]::Ordinal) } | Group-Object archive | ForEach-Object {
            [ordered]@{
                archive = $_.Name
                index_json = $_.Group[0].index_json
                is_update_archive = $_.Name -match '[\\/]Saved[\\/]'
                mount_priority_verified = $false
                selected = $false
                entries = @($_.Group | ForEach-Object entry)
            }
        })
        [ordered]@{
            name = $name
            virtual_package = ($packagePath -replace '^\.\./\.\./\.\./NZM/Content/', '/Game/')
            cooked_package = ($packagePath -replace '^\.\./\.\./\.\./', '')
            reference_evidence = [ordered]@{
                file = 'Y:\网络游戏\逆战：未来\NZM\Saved\Logs\NZM.log'
                decoded_line_number = 5393
                log_timestamp = '2026.11.03-08.16.03:100'
                encoding = 'XOR-0x73'
                event = 'RequestAsyncLoad called with both valid and null assets'
                meaning = 'Reference occurrence only; successful load and values are unverified.'
            }
            versions = $versions
            patch_copies_found = @($versions | Where-Object is_update_archive).Count
            extraction_or_values_verified = $false
        }
    }
})
$report = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    status = 'offline_index_targets_only'
    source_locator = Join-Path $indexRoot 'environment_map_locators.json'
    source_map_locator_count = $maps.Count
    pc_hd_named_map_count = @($maps | Where-Object { $_.entry.path -match '(?i)PC_HD' }).Count
    exact_hd_map_note = 'Only the standard background has an explicit _HD suffix among these role targets. This does not establish a PC_HD runtime route or better output.'
    archive_precedence = 'unresolved; all occurrences retained; no archive or patch selected'
    limitations = @(
        'Only pre-existing index JSON and locator records were read. No process access, enumeration, decryption, archive reindexing, or game writes were performed.',
        'Names and directories establish candidate identity only, not light values, quality, runtime selection, or full dependencies.',
        'Geometry/HDR/material dependencies need to be followed from deserialized map exports.',
        'Bianca EditorOnly availability and the Naimei/Queen branches need serialized scene-routing evidence.'
    )
    scenes = $scenes
    data_table_index_scan_count = $allIndexFiles.Count
    data_table_match_method = 'Exact filename stem for uasset/uexp/ubulk across existing Pak JSON files; no patch precedence inferred.'
    data_table_targets = $tables
}
$output = Join-Path $AnalysisRoot 'scene_parameter_targets.json'
[IO.File]::WriteAllText($output, ($report | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
[ordered]@{
    output = $output
    scene_count = $scenes.Count
    minimum_map_target_count = @($scenes | ForEach-Object minimum_parameter_targets).Count
    follow_up_map_target_count = @($scenes | ForEach-Object follow_up_targets).Count
    table_targets = $tables.Count
    index_json_scanned = $allIndexFiles.Count
    table_patch_copies = @($tables | ForEach-Object versions | Where-Object is_update_archive).Count
} | ConvertTo-Json
