package com.spatulox.mcbumptest;

import java.util.ArrayList;
import java.util.List;
import net.fabricmc.api.ModInitializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The smallest mod that can exercise mc-bump end to end.
 *
 * <p>It touches no registry on purpose. What is under test is the PIPELINE — does the
 * jar build for every claimed version, does the loader load it, does the log say what
 * the config claims it says — and a mod that registered real content would only make
 * that slower and more fragile.
 *
 * <p>The markers below are what {@code .github/mc-bump.yml} asserts on. Their count is
 * derived from this very file by {@code expect-count}, so declaring one more is enough
 * to move what the pipeline expects.
 *
 * <p>Careful with the wording here: {@code count-pattern} is a plain substring, matched
 * with the equivalent of {@code grep -cF}, so it counts comment lines too. Spelling the
 * pattern out in this Javadoc made the count read one too high — caught by the very
 * check it documents.
 *
 * <p>{@code MCBUMP_BREAK} exists so the self-test can drive a failure without editing
 * any file: that is how the failure report itself gets tested.
 */
public class TestMod implements ModInitializer {

    public static final String MOD_ID = "mc-bump-testmod";

    private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
    private static final List<String> MARKERS = new ArrayList<>();

    public static final String ALPHA = marker("alpha");
    public static final String BETA = marker("beta");
    public static final String GAMMA = marker("gamma");

    private static String marker(String name) {
        MARKERS.add(name);
        return name;
    }

    /**
     * What the self-test asked us to break, or "" for a healthy run.
     *
     * <p>An environment variable rather than a gradle property: the server runs in a
     * JVM the build tool forks, and the environment is the one thing that survives
     * that hop unchanged.
     */
    static String broken() {
        String value = System.getenv("MCBUMP_BREAK");
        return value == null ? "" : value;
    }

    static int reportedCount() {
        // "count" makes the log disagree with the source, which is exactly the
        // expect-count mismatch a real mod hits when a registration is dropped.
        return "count".equals(broken()) ? MARKERS.size() - 1 : MARKERS.size();
    }

    @Override
    public void onInitialize() {
        LOGGER.info("Registered {} markers", reportedCount());

        // "marker" swallows the phrase the config expects, which is how a callback
        // that silently never ran looks from the outside.
        if ("marker".equals(broken())) {
            LOGGER.info("(MCBUMP_BREAK=marker: the completion marker is withheld)");
            return;
        }
        LOGGER.info("Initialisation complete");
    }
}
