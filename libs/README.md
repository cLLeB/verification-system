# libs/ — SourceAFIS runtime jars (not committed)

The SourceAFIS matcher jars are **not stored in git** (binary, ~26 MB, and
re-fetchable). Restore them with Maven into this folder:

```bash
mvn dependency:copy-dependencies \
    -DoutputDirectory=libs \
    -Dartifact=com.machinezoo.sourceafis:sourceafis:3.18.1
```

Or download `com.machinezoo.sourceafis:sourceafis:3.18.1` and its transitive
dependencies from Maven Central. The engine loads everything in `libs/*.jar`
via JPype (see `fingerprint/sourceafis.py`). Requires Java 21 + `JAVA_HOME` set.
