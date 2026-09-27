<question>
{task_description}
</question>

<agent_conversation>
{transcript}
</agent_conversation>

<workspace_state>
{workspace_state}
</workspace_state>

<verifier_output>
{verifier_output}
</verifier_output>

RUBRIC ({n_criteria} criteria — produce one verdict per item, in this order):
{rubrics_block}

Now produce the <judgment>...</judgment> block with exactly {n_criteria} verdicts in the format specified by the system prompt.
