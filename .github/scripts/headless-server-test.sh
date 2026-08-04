#!/usr/bin/env bash
#
# Lance un serveur Minecraft dedie headless avec le mod, et verifie qu'il
# atteint "Done" sans crasher. Utilisable en local :
#
#   bash .github/scripts/headless-server-test.sh
#
# Variables d'environnement :
#   RUN_DIR      repertoire de run de loom          (defaut: run)
#   LOG          fichier de log produit             (defaut: server-test.log)
#   BOOT_TIMEOUT secondes avant abandon du demarrage (defaut: 900)
#
set -euo pipefail

RUN_DIR="${RUN_DIR:-run}"
LOG="${LOG:-server-test.log}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-900}"
STOP_TIMEOUT="${STOP_TIMEOUT:-60}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Nombre de potions attendu, deduit du code source pour rester juste quand on
# en ajoute (plutot qu'une constante a maintenir en double).
MOD_SOURCE="src/main/java/com/spatulox/ExtendedTimePotion.java"
EXPECTED_POTIONS="${EXPECTED_POTIONS:-$(grep -c '= registerPotion(' "$MOD_SOURCE")}"

FIFO_DIR="$(mktemp -d)"
FIFO="$FIFO_DIR/server-stdin"
GRADLE_PID=""

cleanup() {
    if [ -n "$GRADLE_PID" ] && kill -0 "$GRADLE_PID" 2>/dev/null; then
        kill "$GRADLE_PID" 2>/dev/null || true
    fi
    exec 3>&- 2>/dev/null || true
    rm -rf "$FIFO_DIR"
}
trap cleanup EXIT

fail() {
    echo ""
    echo "=== ECHEC : $* ==="
    echo "--- 200 dernieres lignes de $LOG ---"
    tail -n 200 "$LOG" 2>/dev/null || echo "(pas de log)"
    exit 1
}

# --- preparation du repertoire de run -------------------------------------
mkdir -p "$RUN_DIR"
printf 'eula=true\n' > "$RUN_DIR/eula.txt"

# monde plat + watchdog desactive : demarrage rapide, pas de faux positif sur
# un runner CI lent.
cat > "$RUN_DIR/server.properties" <<'PROPS'
online-mode=false
level-type=minecraft\:flat
level-name=ci-smoke-test
max-tick-time=-1
view-distance=4
simulation-distance=4
sync-chunk-writes=false
spawn-protection=0
PROPS

rm -f "$LOG"
mkfifo "$FIFO"
# Ouverture en lecture-ecriture (3<>) et non en simple ecriture (3>) :
# ouvrir un FIFO en ecriture seule BLOQUE tant qu'aucun lecteur n'est present,
# or le lecteur (gradle) n'est lance qu'apres. Le mode <> ne bloque jamais, et
# garde le cote ecriture ouvert pour que le serveur ne voie pas un EOF immediat
# sur son stdin.
exec 3<> "$FIFO"

echo "==> Demarrage du serveur (timeout ${BOOT_TIMEOUT}s)..."
./gradlew runServer --no-daemon --console=plain --stacktrace < "$FIFO" > "$LOG" 2>&1 &
GRADLE_PID=$!

# --- attente du demarrage -------------------------------------------------
started=0
elapsed=0
while [ "$elapsed" -lt "$BOOT_TIMEOUT" ]; do
    if grep -qE 'Done \([0-9.]+s\)' "$LOG" 2>/dev/null; then
        started=1
        break
    fi
    if ! kill -0 "$GRADLE_PID" 2>/dev/null; then
        # le process est mort avant d'avoir affiche "Done"
        break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

if [ "$started" -ne 1 ]; then
    if kill -0 "$GRADLE_PID" 2>/dev/null; then
        fail "le serveur n'a pas atteint \"Done\" en ${BOOT_TIMEOUT}s"
    else
        fail "le serveur s'est arrete avant d'avoir demarre"
    fi
fi

echo "==> Serveur demarre."

# --- verifications sur le log --------------------------------------------
if ! grep -q 'extended-time-potion' "$LOG"; then
    fail "le mod extended-time-potion n'apparait pas dans le log de chargement"
fi

# La version reellement demarree doit correspondre a gradle.properties. Sans
# ce garde-fou, un cache loom perime ou une modification concurrente du fichier
# ferait passer le test au vert sur la mauvaise version de Minecraft.
EXPECTED_MC="$(sed -n 's/^minecraft_version=//p' gradle.properties | head -n 1 | tr -d '[:space:]')"
BOOTED_MC="$(sed -n 's/.*Starting minecraft server version \(.*\)$/\1/p' "$LOG" | head -n 1 | tr -d '[:space:]')"
echo "==> Version attendue : ${EXPECTED_MC:-?} | version demarree : ${BOOTED_MC:-?}"
if [ -z "$BOOTED_MC" ]; then
    fail "impossible de lire la version demarree dans le log"
fi
if [ "$BOOTED_MC" != "$EXPECTED_MC" ]; then
    fail "le serveur a demarre Minecraft $BOOTED_MC alors que gradle.properties demande $EXPECTED_MC"
fi

# grep cible : Minecraft logue plein de WARN benins, on ne cherche que les
# signatures d'echec reelles.
FATAL_PATTERNS='Mixin apply failed|Failed to load mod|Could not execute entrypoint|A potential solution has been determined|Incompatible mod set'
if grep -qE "$FATAL_PATTERNS" "$LOG"; then
    echo "--- lignes fatales detectees ---"
    grep -nE "$FATAL_PATTERNS" "$LOG" || true
    fail "erreur fatale detectee dans le log"
fi

# --- le mod a-t-il reellement fonctionne ? --------------------------------
# "le serveur demarre" ne prouve pas que le mod fait son travail : un registre
# vide ou un callback de brassage qui n'a jamais tourne passeraient inapercus.
# Ces deux marqueurs sont emis par ExtendedTimePotion.onInitialize().
if ! grep -q 'Brewing mixes registered' "$LOG"; then
    fail "le callback de brassage de Fabric API n'a pas tourne (FabricPotionBrewingBuilder casse ?)"
fi

POTIONS="$(sed -n 's/.*Registered \([0-9]\{1,\}\) potions.*/\1/p' "$LOG" | head -n 1)"
if [ -z "$POTIONS" ]; then
    fail "le mod n'a pas signale le nombre de potions enregistrees"
fi
echo "==> Potions enregistrees : $POTIONS (attendu : $EXPECTED_POTIONS)"
if [ "$POTIONS" -ne "$EXPECTED_POTIONS" ]; then
    fail "$POTIONS potions enregistrees au lieu de $EXPECTED_POTIONS"
fi

# --- arret propre ---------------------------------------------------------
echo "==> Envoi de la commande stop..."
echo "stop" >&3 || true

waited=0
while [ "$waited" -lt "$STOP_TIMEOUT" ] && kill -0 "$GRADLE_PID" 2>/dev/null; do
    sleep 2
    waited=$((waited + 2))
done

if kill -0 "$GRADLE_PID" 2>/dev/null; then
    # loom ne redirige pas toujours System.in vers le serveur. Le serveur a
    # demarre sans crash, l'objectif du test est atteint : on tue et on passe.
    echo "==> Le serveur n'a pas repondu a \"stop\" en ${STOP_TIMEOUT}s, arret force."
    kill "$GRADLE_PID" 2>/dev/null || true
    wait "$GRADLE_PID" 2>/dev/null || true
else
    wait "$GRADLE_PID" 2>/dev/null || true
    echo "==> Serveur arrete proprement."
fi

GRADLE_PID=""
echo ""
echo "=== OK : le serveur a demarre avec le mod sans erreur fatale ==="
grep -E 'Done \([0-9.]+s\)' "$LOG" | tail -n 1
