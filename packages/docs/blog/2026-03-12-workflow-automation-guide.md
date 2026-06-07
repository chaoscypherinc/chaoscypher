---
slug: workflow-automation-guide
title: "Automate Your Knowledge Pipeline: Triggers, Workflows, and AI Tools"
authors: [denis]
tags: [workflows, tutorials]
date: 2026-03-12
description: Build event-driven knowledge pipelines in Chaos Cypher using triggers, multi-step workflows, and composable AI tools — no manual babysitting required.
---

Most knowledge management tools treat you like a filing clerk. Upload a document, wait for extraction, manually review entities, fix errors, tag things, connect things. Then do it all again for the next document. And the next. And the next fifty.

<!-- truncate -->

This is fine when you have ten documents. It falls apart at a hundred. It becomes genuinely painful at a thousand. The bottleneck is never the AI -- it's the human loop. Every document requires your attention, your judgment calls, your clicks. The extraction might take thirty seconds. Your review and cleanup take ten minutes.

Chaos Cypher's workflow engine exists to close that gap. You define a processing pipeline once -- what to extract, how to validate it, where to send notifications -- and every new document flows through it automatically. No babysitting. No repetitive clicking. You set the rules, the system follows them.

This isn't a cron job bolted onto the side. It's a proper workflow engine with event-driven triggers, conditional branching, step-to-step data passing, and composable AI tools. Let me walk you through how it works.

## A Concrete Workflow: Auto-Processing Research Papers

Abstractions are boring. Let's look at a real workflow you might build: automatically processing research papers as they're uploaded.

Here's the pipeline:

**Trigger:** A new source file is uploaded (`file.upload` event fires).

**Step 1 -- AI Prompt:** Summarize the document in three sentences. The `ai.prompt` tool sends the document text to your configured LLM with instructions to produce a concise summary. If the document is long, it automatically chunks the text and processes sections in parallel, then merges the results.

**Step 2 -- AI Extract JSON:** Pull out structured metadata. Authors, publication date, journal name, key findings, methodology type. The `ai.extract_json` tool takes the document text and a JSON schema defining exactly what you want, then returns validated structured data. It retries if the extraction doesn't match the schema.

**Step 3 -- Conditional:** Check if this is a clinical study. The `logic.conditional` tool evaluates whether `{{steps.step_2.methodology_type}}` equals `"clinical_trial"`. If true, the workflow branches to run additional [medical-domain extraction](/blog/domain-extraction-guide). If false, it skips ahead.

**Step 4 -- HTTP Request:** Post a notification to a Slack webhook with the summary from Step 1 and the metadata from Step 2. The `http.request` tool sends a POST to your webhook URL with a JSON body containing `{{steps.step_1.result}}` and `{{steps.step_2.extracted_data}}`.

Notice the `{{steps.step_1.result}}` syntax. That's the interpolation engine at work. Every step's output is available to every subsequent step via dot-notation paths. You can reference `{{inputs.document_text}}` for the original trigger data, `{{steps.step_2.extracted_data.authors}}` for a nested field from a previous step, or even `{{steps.step_3.branch_taken}}` to see which conditional path was followed. The interpolation preserves types too -- if a previous step returned a number, you get a number, not the string `"42"`.

This entire pipeline runs without human intervention. Upload a PDF, walk away, come back to a summarized, metadata-tagged, conditionally-processed document with a Slack notification waiting for you.

![Workflow list showing automation with status and controls](/img/screenshots/workflows-list.png)

![Queue monitor showing task status and history](/img/screenshots/queue-monitor.png)

## Under the Hood: 10 Built-In Tools

The workflow engine ships with ten built-in tools organized into five categories. Each tool has a defined input schema and output schema, so the system validates your configuration before anything runs.

| Category | Tools | What They Do |
|----------|-------|-------------|
| **AI** | `ai.prompt`, `ai.extract_json`, `ai.vector_search`, `ai.generate_embedding` | LLM interactions with chunking support, structured JSON extraction with schema validation and retries, semantic search across your knowledge graph, vector embedding generation for entities |
| **Data** | `data.extract`, `data.merge` | Pull values from nested objects using dot-notation paths (`user.addresses.0.city`), merge multiple dictionaries with shallow or deep strategies |
| **Logic** | `logic.conditional`, `logic.loop` | If/then branching with safe expression evaluation, iterate over collections with configurable limits |
| **HTTP** | `http.request` | External API calls with all HTTP methods, bearer/basic auth, configurable timeouts, and SSRF protection that blocks localhost access |
| **Templates** | `templates.list` | Query your knowledge graph schema to discover available node templates |

A few things worth highlighting about specific tools:

**`ai.prompt`** is smarter than a simple LLM call. It supports chunk strategies (`quick` and `full`) for documents that exceed the model's context window. When chunking is enabled, it splits the document on paragraph boundaries, processes each chunk in parallel via the LLM queue, and intelligently merges the results -- concatenating text outputs, extending arrays, and merging objects.

**`ai.extract_json`** enforces structure. You provide a JSON schema defining what you expect (say, `{"entities": [{"name": "string", "type": "string"}]}`), and the tool validates the LLM's output against it. If the output doesn't match, it retries automatically. This makes extraction reliable enough to run unattended.

**`ai.vector_search`** lets workflows query the knowledge graph semantically. Give it a natural language query and it performs hybrid search -- combining vector similarity with keyword fallback -- to find matching nodes. You can filter by template type and set a similarity threshold. This is how you build workflows that reason about existing knowledge: "find all entities similar to what we just extracted and check for duplicates."

**`http.request`** has built-in security. URLs are validated before any request is sent -- only `http` and `https` schemes are allowed, and direct `localhost` access is blocked to prevent SSRF attacks. It supports all standard HTTP methods (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS), bearer and basic authentication, custom headers, and JSON or string request bodies. Since Chaos Cypher runs in Docker, access to other containers via their service names works fine.

### How Triggers Work

Triggers are the entry point for automated workflows. They listen for events in the system and fire workflows when conditions are met.

**Event sources** define what happened: `node.create` (a new node was added to the graph), `node.update` (an existing node was modified), `file.upload` (a new source file was uploaded), `import.completed` (a batch import finished). The system ships with built-in triggers for auto-embedding -- every time a node is created or updated, a workflow automatically generates vector embeddings for it.

**Filters** let you narrow the scope. A trigger on `node.create` with a filter `{"template_id": "person_template"}` only fires when a Person node is created, not when any node is created. Filters use exact key-value matching against the event data.

**Statistics tracking** gives you visibility. Every trigger execution records success/failure status, execution time, and error messages. You can see your success rate, average execution time, and recent execution history -- useful for debugging workflows that occasionally fail.

### The "Expose as AI Tool" Feature

Here's where things get composable. Any workflow can be exposed as a callable AI tool by setting `expose_as_ai_tool: true` and defining input/output schemas. Once exposed, that workflow appears alongside the built-in tools and can be used as a step in other workflows.

Think about what this enables. You build a workflow that extracts and validates medical terminology. You expose it as a tool. Now your "process research papers" workflow can call it as Step 3 instead of hardcoding medical-domain logic. You have a workflow that enriches person entities by cross-referencing external APIs? Expose it, and any other workflow can use it.

Workflows calling workflows. Each one focused on a single job, composed together into pipelines of arbitrary complexity. The step type `workflow` (alongside `system_tool` and `user_tool`) tells the engine to execute another workflow as a step, passing inputs and receiving outputs just like any other tool.

### Workflow Portability

Workflows are portable. You can export any workflow to a version-stamped JSON file that includes the workflow definition, all its steps, and their configurations. Import it into another Chaos Cypher instance -- or share it with someone else running their own instance.

The import process is deliberate about safety. Before importing, the system validates the export version for compatibility and checks that all referenced tools exist in the target instance. It walks through every step, resolves each `tool_id` against the registry of system tools and user tools, and fails early if anything is missing. If a workflow references `ai.prompt` and `http.request`, those tools must be available. If a custom tool plugin is missing, the import fails with a clear error message rather than creating a broken workflow.

This design means workflows are self-describing and portable. The JSON file contains everything needed to reconstruct the workflow -- no hidden state, no implicit dependencies on database IDs. Export from your laptop, import on a server, share with a colleague. The only requirement is that the target instance has the same tools installed.

## Try It Yourself

The fastest way to see the workflow engine in action is to look at the export format. Here's a minimal workflow that summarizes documents on upload:

```json
{
  "version": "1.0",
  "workflow": {
    "name": "Summarize on Upload",
    "description": "Auto-summarize new documents when uploaded",
    "input_schema": {
      "type": "object",
      "properties": {
        "document_text": {
          "type": "string",
          "description": "The document content to summarize"
        }
      },
      "required": ["document_text"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "string",
          "description": "Three-point summary of the document"
        }
      }
    }
  },
  "steps": [
    {
      "step_number": 1,
      "name": "Summarize Document",
      "tool_type": "system_tool",
      "tool_id": "ai.prompt",
      "configuration": {
        "prompt": "Summarize this document in 3 key points:\n\n{{inputs.document_text}}",
        "output_format": "text"
      }
    }
  ]
}
```

This is everything the system needs. The `version` field ensures forward compatibility. The `input_schema` and `output_schema` define the contract. The `steps` array contains the pipeline.

Each step specifies its `tool_type` (`system_tool`, `user_tool`, or `workflow`), a `tool_id` that references a registered tool, and a `configuration` object whose shape matches the tool's input schema. The `{{inputs.document_text}}` template variable gets resolved at execution time with the actual trigger data.

When importing, you have three options for handling name conflicts:

- **`fail`** -- refuse to import if a workflow with the same name exists (the default, prevents accidental overwrites)
- **`skip`** -- silently keep the existing workflow and skip the import
- **`rename`** -- import with ` (imported)` appended to the name

You can also import as inactive (`import_as_inactive: true`) to test a workflow before enabling it in production. This creates the workflow with `is_active: false`, letting you review the steps and do a manual test run before flipping it on.

![Settings page with import and export graph options](/img/screenshots/settings-general.png)

![Queue monitor with task tracking and auto-refresh](/img/screenshots/queue-monitor.png)

To set up the trigger, create a trigger record with the event source (like `file.upload`), link it to your workflow, and optionally add filters. The trigger system runs as a background event loop -- events are queued and processed asynchronously, so trigger evaluation never blocks the main API.

For more complex workflows, the step dependency system lets you control execution order beyond simple sequential numbering. Each step can declare `depends_on` (a list of step IDs that must complete before it runs) and `continue_on_error` (proceed even if the step fails). You can also set `retry_on_failure` to have the engine retry a step automatically, and `timeout_seconds` to cap how long any individual step can run. Combined with `logic.conditional` for branching and `logic.loop` for iteration, you can express surprisingly sophisticated pipelines.

The execution model tracks everything. Each workflow run produces an execution record with status (pending, running, completed, failed, cancelled), the inputs that were provided, the outputs that were produced, timing data for each step, and -- critically -- which step failed and why if something goes wrong. This execution history is what makes workflows debuggable. When a workflow fails at 3am, you don't have to guess what happened. You look at the execution detail, see that Step 3 timed out after 120 seconds waiting for the LLM, and adjust accordingly.

## What's Next

The workflow engine is designed to grow. The tool system uses a plugin architecture -- the same pattern that powers Chaos Cypher's loader plugins, domain plugins, and LLM providers. Custom tool plugins in Python are on the roadmap for users who need capabilities beyond the built-in ten. Implement a class with `tool_id`, `input_schema`, `output_schema`, and an `execute` method, drop it in the plugins directory, and it auto-registers.

More trigger event sources are coming as the platform grows. Scheduling (run a workflow every Tuesday at 9am) and webhook triggers (fire a workflow from an external system) are natural extensions of the existing event-driven architecture.

If you've built an interesting automation workflow -- whether it's a multi-step research pipeline, a quality assurance checker, or an integration with external tools -- I'd genuinely like to hear about it. The export format makes sharing straightforward: export your workflow, share the JSON, and someone else can import it and adapt it to their use case. That's the whole point of portability.

For the full API reference and detailed configuration options, check out the [workflow documentation](/docs/reference/api/workflows). The built-in system workflows (like auto-embedding on node create/update) are also good starting points -- export them and study the step configurations to see how the engine's own automation is wired together.