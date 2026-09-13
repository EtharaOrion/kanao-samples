# Repair the delivery pipeline

A five phase delivery pipeline (lint, build, test, package, publish) is threaded through a trusted engine named cicd that records each phase onto an accumulating carry at /workspace/state/carry.json and can print a status through cicd report. The pipeline is driven by the tooling under /workspace/bin.

Something is wrong with the pipeline. When it is run end to end the engine reports that the pipeline is not complete even though every phase appears to run, and phases that ran early are missing from the recorded history by the time the run finishes. The engine is trusted and correct; the fault is in how the pipeline is driven across the run.

Find the cause and repair it so a full run leaves the engine reporting a complete pipeline, with all five phases threaded in order onto one carry and no early phase lost. You may edit files under /workspace/bin and state under /workspace/state. You must not edit the trusted engine, and you must not hand write or otherwise fabricate the carry or the report; a phase counts only when the engine itself records it.
