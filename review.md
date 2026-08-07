# Revue de code — mc-bump

Portée : l'intégralité du dépôt, à l'état du commit `2af9740` (« feat(config): glob
patterns by default, and stop counting comments »), qui a atterri pendant la lecture.

---

## 1. Problèmes réels, par ordre d'importance

### P1 — Le fixture `testmod/` n'est branché sur rien

`testmod/fabric/` existe, avec un hook de panne délibéré (`MCBUMP_BREAK=marker|count`,
`TestMod.java:53`) et une config qui l'exploite (`testmod/fabric/.github/mc-bump.yml:43`).
Aucun workflow ne le lance : `self-test.yml` fait unittest + validation du README +
`--help` + actionlint, et ne mentionne jamais `testmod`.

Le commit `0f2eb2b` promet « mc-bump can prove itself » — la promesse n'est pas tenue,
et c'est exactement le reproche que `ci.yml:226` fait aux autres (« an unrun test rots
silently »). C'est le trou le plus coûteux : rien ne prouve aujourd'hui que la chaîne
build → boot → `check_log` → rapport fonctionne bout en bout.

### P2 — Injection dans `$GITHUB_OUTPUT` (vérifié)

`config.export_github_output` (config.py:367) et `emit_github_output`
(mc-bump.py:63-73) écrivent `clé=valeur` brut. Testé :

```yaml
notify:
  label: |-
    boom
    ci=true
```

→ produit une ligne `ci=true` parasite dans la sortie. Une valeur multi-lignes peut donc
réécrire n'importe quel autre output (y compris `ci`, `release`, `stores`). C'est
auto-infligé (le mod possède sa config), mais c'est un « vert silencieux », le mode de
panne que ce dépôt existe pour empêcher.

**Correctif :** la forme heredoc documentée par GitHub (`clé<<DELIM`), ou refuser un
`\n` dans `Field.coerce`.

### P3 — Interpolation shell → Python dans `.github/actions/setup/action.yml:118-121`

```bash
TAG="$(PYTHONPATH=.mc-bump python3 -c "
from lib.config import load
print(load().tag_for('$MOD_VERSION'))
")"
```

`$MOD_VERSION` vient de `gradle.properties` et est collé dans un littéral Python par le
shell. Une apostrophe casse le run ; une valeur fabriquée exécute du code sur le runner.

Même classe, plus légère : `report-issue/action.yml`,
`select(.title == \"$TITLE\")` où `$TITLE` contient `$GITHUB_REF_NAME`.

**Correctif :** passer par `env:` et lire `os.environ`.

### P4 — Le schéma de `gradle.properties` est obligatoire mais non documenté

`set_property` lève quand la clé manque (gradle.py:62-67). Vérifié :

```
Failure: key 'loom_version' not found in gradle.properties
```

Sont requis de fait : `minecraft_version`, `mod_version`,
`supported_minecraft_versions`, `java_version`, plus `loom_version` /
`loader_version` / `fabric_api_version`. Le README ne documente que `mc-bump.yml`.

S'y ajoute un contrat implicite : le `build.gradle` du mod **doit** honorer
`-Pminecraft_version` (matrix.py:86, 131), sans quoi la matrice construit trois fois la
même version en restant verte.

### P5 — `check_log` compile les patterns trop tard

server_test.py:129-135, 147, 159-164 : les globs sont compilés à chaque appel, donc
**après** le boot du serveur. Un `<count>` mal placé ou un `regex:` invalide coûte
~10 min de CI avant de se signaler.

`config.load()` valide déjà `mod_loaded_pattern` à cette fin précise (config.py:341) —
les patterns de `expect` / `expect-count` / `fatal-extra` devraient l'être au même
endroit, et le `Matcher` porté par le `Project`.

### P6 — `next()` sans défaut

matrix.py:248 : `role = next(r for r, key in loader.gradle_keys.items() if key == rung.gradle_key)`.
Un loader dont un `Rung` référence une clé absente de `gradle_keys` produit un
`StopIteration` nu au lieu d'un `Failure` propre. Disparaît si `Rung` porte le rôle
(cf. §4).

### P7 — Divers

- `server_test.py:139` appelle `matcher._match(...)`, méthode privée d'une autre classe.
  `Matcher` doit exposer un `matches(line)`.
- `server_test.py:113-115` : `expected` peut être `None` → message
  « booted 26.2 while None was expected ».
- `config.py:25-30` : `raise SystemExit(1)` **à l'import** d'un module de bibliothèque,
  importé par tous les scripts et par les `python -c` des workflows. Aucun appelant ne
  peut traiter l'erreur autrement.
- `common.http_get` (common.py:29-52) : aucun retry. Modrinth renvoie 429 au-delà de
  ~300 req/min et l'échelle d'escalade boucle sur `resolve_one`. Un 503 ponctuel casse
  l'auto-update hebdomadaire.
- `report.py:51` : `rank` annoté `-> int`, renvoie un tuple.
- `versions.py:31` : `_manifest_cache` global, non réinitialisable.

---

## 2. Code mort

| Où | Quoi |
|---|---|
| `loaders/base.py:52` | `Rung.flag` — écrit par fabric.py:70-71, **jamais lu** (les flags CLI sont en dur dans mc-bump.py:150-160). |
| `matrix.py:283` | `pin_property` — docstring « used by the tests », aucun test ne l'appelle. |
| `config.py:378` + `setup/action.yml:47-49` | `matrix_parallel` exporté partout, lu par aucun workflow. `tests.matrix.parallel` est un bouton mort de la config publique. |
| `config.py:232` | `notify.keep-branch` — dans le schéma et le README, jamais lu ; le comportement est câblé (`git push --force`, jamais de suppression, auto-update.yml:406-408). |
| `config.py:165` | `mod.package` — « informational », jamais lu. |
| `loaders/base.py:132` | `store_loader_name()` — seul consommateur `export_json`, que la CI ne lit pas. La publication délègue au gradle du mod, donc le nom du loader n'atteint jamais le store. |

---

## 3. Factorisation

### Le plus rentable

1. **Le job `matrix` est dupliqué à l'identique** entre `ci.yml:143-223` et
   `release.yml:150-226` — ~75 lignes chacun (double checkout, setup-java,
   setup-gradle, pip, build, headless, assemblage du log, report, upload, fail).
   → une composite `.github/actions/build-and-boot`.

2. **Trois parseurs pour deux formats ad-hoc.** `test-escalation.txt` est écrit par
   matrix.py:235, relu par `report.escalation_table` (report.py:263) *et* par un `awk`
   dans auto-update.yml:272 **et** :291. Idem `test-matrix-status.txt` (matrix.py:79 ↔
   report.py:166-171, positionnel).
   → JSON, un seul lecteur Python, une sous-commande `lib.report` pour le workflow.
   C'est aussi le blocage n°1 pour la matrice 2-D du multiloader (§5).

3. **`prop()` en `sed`** redéfini dans setup/action.yml:108 et release.yml:379, alors
   que `gradle.read_property` fait déjà ça.
   → `python3 -m lib.gradle --get`, ou mieux : `lib.config --github-output` sort déjà
   tout le reste, qu'il sorte aussi `minecraft_version` / `mod_version` / `java_version`.

4. **`publish-modrinth` et `publish-curseforge`** (release.yml:228-349) sont le même job
   à trois chaînes près (nom, tâche gradle, secret).
   → un seul job en `strategy.matrix.store`. Aujourd'hui ajouter un troisième store
   demande un job complet **plus** une entrée dans `KNOWN_STORES` (config.py:238).

5. **Les noms de logs** `build-{v}.log` / `server-test-{v}.log` sont en dur dans
   matrix.py:117-118, report.py:188/193 et les trois workflows.
   → constantes partagées.

### Plus léger

- `pip install pyyaml` répété dans 5 endroits.
- `mc-bump.py` relit `gradle.properties` 4 fois (:265, :392, :415) là où un petit objet
  `Properties` avec `.get/.set/.save(dry_run)` suffirait.
- `Field.coerce` (config.py:76-106) est un if/elif sur `self.type`.
- `release.stores` est validé hors schéma (config.py:331-336) faute d'un
  `Field(list, item_choices=…)`.

---

## 4. Étendre à d'autres loaders

L'intention est bonne — la couture `lib/loaders/base.py` est au bon endroit, et
`versions.py` est effectivement agnostique. Mais **cinq choses trahissent le
mono-loader Fabric** et empêchent NeoForge d'être « juste un fichier de plus » :

**a) `Resolved.usable` exige `loader` ET `api`** (base.py:43-44). NeoForge n'a pas
d'artefact d'API séparé — la version `neoforge` est les deux à la fois. Forge non plus.
Avec le code actuel, un `NeoForgeLoader` renverrait `usable == False` en permanence et
l'auto-update dirait éternellement « pas encore publié ».
→ `Resolved` doit porter un `dict[role, version]` et le loader décider de `usable`.

**b) Les rôles sont écrits en dur, quatre fois hors du loader** : `("loader", "api")` en
update.py:113, :117, :279, et `('loader', 'api', 'buildtool')` dans auto-update.yml:252.
→ `Loader.frozen_roles()` et itérer dessus.

**c) L'échelle d'escalade est limitée à deux barreaux par la CLI.** `--bump-api` /
`--bump-loader` sont deux flags argparse figés (mc-bump.py:150-160, 216-217), et
`Rung.flag` — qui existait pour ça — est mort. Quilt (QFAPI + QSL + loader) ne rentre
pas.
→ `Rung(role=…, label=…)`, un seul `--bump <role>` dont les `choices` viennent du
loader. Ça supprime aussi le `next()` de P6.

**d) `write_json` impose l'indentation tab « convention Fabric »** (gradle.py:93-100) et
est utilisé par `fabric.py`. `neoforge.mods.toml` est du TOML : l'écrire proprement
demande `tomlkit` (la stdlib ne sait que lire, depuis 3.11). C'est la seule vraie
décision de dépendance à trancher — aujourd'hui le README promet « python3 et pyyaml,
rien d'autre ».

**e) Les clés génériques de `gradle.properties` sont en dur** (`minecraft_version`,
`mod_version`, `supported_minecraft_versions`, `java_version`). Ça passe pour NeoForge,
dont les templates suivent la même convention, mais ça devrait être une table du loader
ou de la config.

### Ordre de refactor suggéré

Chacun est petit, testable, et livrable seul :

```
1. Rung.role au lieu de Rung.flag ; --bump <role> ; supprimer le next()
2. Loader.frozen_roles() ; Resolved en dict de rôles ; usable délégué
3. Loader.property_keys() couvrant aussi les clés génériques
4. Compiler les Matcher dans config.load()  (règle P5 au passage)
```

Rien là-dedans n'a besoin du multiloader, et tout est nécessaire pour lui.

---

## 5. Multiloader (un dépôt, plusieurs loaders — style Architectury)

C'est un saut plus grand, parce que l'hypothèse structurante n'est pas dans les
loaders : **`Project` = 1 racine, 1 loader, 1 `ModPaths`** (config.py:282-300,
gradle.py:28-39). Un dépôt Architectury a :

```
gradle.properties            partagé : minecraft_version, mod_version…
common/
fabric/    src/main/resources/fabric.mod.json
neoforge/  src/main/resources/META-INF/neoforge.mods.toml
```

avec des tâches `:fabric:runServer`, `:neoforge:build`.

### Ce qu'il faut introduire

Un `Target = (loader, chemin du sous-projet gradle, ModPaths)`, et
`Project.targets: list[Target]`. La config passe de `loader: fabric` à une liste, en
gardant le scalaire comme raccourci à un élément — les configs existantes continuent de
charger.

### Ce qui casse en cascade

- `server_task()` / `client_gametest_task()` → préfixés `:{target.path}:`, et le build
  de matrix.py:84-88 aussi.
- **La matrice devient 2-D** : versions × loaders. `VersionOutcome` (matrix.py:56) gagne
  une colonne, donc `test-matrix-status.txt` change de format — d'où l'urgence du point
  3.2 (passer en JSON **avant**, pas pendant).
- **L'escalade devient par loader** : `fabric_api_version` et `neoforge_version` bougent
  indépendamment. L'invariant « une mise à jour ne déplace qu'une variable » survit, mais
  devient « une variable **par loader** », et `run_with_escalation` (matrix.py:215) doit
  boucler.
- `mark_supported` (update.py:289) écrit une plage par fichier de métadonnées → boucle
  sur les targets.
- `report.pr_body` (report.py:290-311) a une signature qui code en dur `loader_name` /
  `api_name` / `buildtool_name` : trois lignes de tableau figées. → une liste de lignes.
- **Côté GitHub** : `strategy.matrix` devient `{minecraft, loader}`, mais **pas un
  produit cartésien** — Fabric supporte souvent une version de Minecraft des semaines
  avant NeoForge. L'action `setup` doit donc émettre une liste de **paires**
  (`include:`), pas deux listes indépendantes. C'est le point où la résolution
  `Loader.resolve()` par version, déjà en place, sert vraiment.

### Le vrai risque conceptuel

Aujourd'hui, la promesse est « un jar couvre une série » (versions.py:1-21). En
multiloader elle devient « un jar **par loader** couvre une série », et
`supported_minecraft_versions` — une chaîne unique dans `gradle.properties` — ne peut
plus décrire les deux si Fabric prouve 26.2 et NeoForge non.

Il faudra soit une liste par loader, soit une règle explicite d'intersection. À trancher
**avant** d'écrire le code, sinon le mécanisme prouve-avant-d'annoncer, qui est le cœur
du projet, devient faux en silence.

---

## Ce qui est solide et à ne pas toucher

Le gel des dépendances + l'échelle d'escalade (un suspect au lieu de trois), le
prouve-avant-d'annoncer, `check_log` pur et testé sans booter, `_terminate` qui tue le
groupe de processus, et le rejet d'un `src/test` vide. Ce sont des décisions correctes
et rares.
