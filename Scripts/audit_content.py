"""Read on-disk UE package dependencies without loading or saving assets."""
import json
from pathlib import Path
import unreal

project = Path(unreal.Paths.project_dir()).resolve()
content = project / 'Content'
registry = unreal.AssetRegistryHelpers.get_asset_registry()
registry.scan_paths_synchronous(['/Game'], True)
options = unreal.AssetRegistryDependencyOptions()
for field in ('include_soft_package_references', 'include_hard_package_references',
              'include_soft_management_references', 'include_hard_management_references'):
    options.set_editor_property(field, True)
roots = ['/Game/Viewer/Maps/L_Viewer', '/Game/Viewer/Data/DA_ViewerCatalog',
         '/Game/Viewer/Environment/DA_Environment', '/Game/Viewer/Materials/M_Stage']
# These two icons are loaded by formatted paths in ViewerWidget.cpp.
# Do not use the entire UI directory as a root: unused icons must remain visible.
roots += ['/Game/Viewer/UI/T_HideUI', '/Game/Viewer/UI/T_Replay']
queue, seen, missing, unknown = list(roots), set(), [], []
while queue:
    package = queue.pop()
    if package in seen or not package.startswith('/Game/'):
        continue
    seen.add(package)
    assets = list(registry.get_assets_by_package_name(package, True))
    if not assets:
        missing.append(package)
        continue
    dependencies = registry.get_dependencies(package, options)
    classes = [str(a.asset_class_path.asset_name) for a in assets]
    if dependencies is None and any(c not in ('Texture2D', 'TextureCube') for c in classes):
        unknown.append({'package': package, 'classes': classes})
    queue.extend(str(p) for p in (dependencies or []))
packages = {}
for path in content.rglob('*'):
    if path.is_file() and path.suffix in ('.uasset', '.umap'):
        package = '/Game/' + path.relative_to(content).with_suffix('').as_posix()
        packages[package] = {'file': path.relative_to(project).as_posix(), 'bytes': path.stat().st_size}
unused = [packages[p] for p in sorted(packages.keys() - seen)]
report = {'roots': roots, 'project_packages': len(packages), 'reachable_packages': len(seen),
          'missing_packages': sorted(missing), 'unknown_dependencies': unknown,
          'unused': unused, 'unused_bytes': sum(p['bytes'] for p in unused)}
report_path = project / 'Saved/ContentAudit.json'
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(
    json.dumps(report, ensure_ascii=False, indent=2))
unreal.log('PUBLISH_CONTENT_AUDIT ' + json.dumps({k:v for k,v in report.items() if k != 'unused'}))

if missing or unknown:
    raise RuntimeError('Incomplete asset dependency audit; see Saved/ContentAudit.json')
