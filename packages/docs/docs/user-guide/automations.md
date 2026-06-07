---
id: automations
title: Automations
description: Build automated knowledge extraction pipelines in Chaos Cypher — create multi-step workflows with AI tools and event triggers to process documents without manual review.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Automations

Automations bring multi-step workflow capabilities to Chaos Cypher, enabling you to build automated knowledge extraction and processing pipelines.

## Workflows

### Managing Workflows

<Tabs>
<TabItem value="web-ui" label="Web UI">


Navigate to **Automations** in the sidebar, then open the **Workflows** tab, to create, view, edit, and delete workflows.

![Workflows page with automation list and controls](/img/screenshots/workflows-list.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# List all workflows
chaoscypher graph workflow list

# View details of a specific workflow
chaoscypher graph workflow get entity-extraction

# Verbose view with step counts
chaoscypher graph workflow list --verbose
```

</TabItem>
<TabItem value="api" label="API">


```
GET    /api/v1/workflows              # List workflows
POST   /api/v1/workflows              # Create workflow
GET    /api/v1/workflows/{id}         # Get workflow
PATCH  /api/v1/workflows/{id}         # Update workflow
DELETE /api/v1/workflows/{id}         # Delete workflow
```

See the [Workflows API Reference](../reference/api/workflows.md) for request/response details and examples.

</TabItem>
</Tabs>


### Workflow Steps

Each workflow contains ordered steps that execute sequentially. Steps reference tool plugins for execution logic.

<Tabs>
<TabItem value="web-ui" label="Web UI">


Use the visual workflow builder to add, configure, reorder, and connect steps using drag-and-drop.

</TabItem>
<TabItem value="api" label="API">


```
GET    /api/v1/workflows/{id}/steps              # List steps
POST   /api/v1/workflows/{id}/steps              # Create step
GET    /api/v1/workflows/{id}/steps/{step_id}    # Get step
PATCH  /api/v1/workflows/{id}/steps/{step_id}    # Update step
DELETE /api/v1/workflows/{id}/steps/{step_id}    # Delete step
PUT    /api/v1/workflows/{id}/steps/reorder      # Reorder steps
```

See the [Workflows API Reference — Steps](../reference/api/workflows.md#workflow-steps) for request/response details.

</TabItem>
</Tabs>


Available tool types for steps include AI tools (prompts, JSON extraction, embeddings, vector search), data tools (extract, merge), logic tools (conditional, loop), HTTP tools, and template tools. See [Tool Plugins](tool-plugins.md) for the full list.

### Workflow Execution

<Tabs>
<TabItem value="web-ui" label="Web UI">


Click **Run** on a workflow to execute it. Progress is displayed in real-time with step-by-step status updates.

![Queue monitor showing task execution status](/img/screenshots/queue-monitor.png)

</TabItem>
<TabItem value="api" label="API">


```
POST   /api/v1/workflows/{id}/executions                    # Execute workflow (returns 202 + execution_id)
GET    /api/v1/workflows/{id}/executions                    # List execution history
GET    /api/v1/workflows/{id}/executions/{eid}              # Get execution details
POST   /api/v1/workflows/{id}/executions/{eid}/cancel       # Cancel execution
GET    /api/v1/workflows/{id}/stats                         # Execution statistics
```

See the [Workflows API Reference — Executions](../reference/api/workflows.md#workflow-executions) for request/response details.

</TabItem>
</Tabs>


### Import/Export

Share workflows between databases or back them up:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Use the export/import buttons on the **Workflows** tab (under **Automations**) to save and load workflow definitions as JSON files.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Export as part of a CCX package
chaoscypher graph package export --output my-workflows.ccx

# Import from a CCX package
chaoscypher graph package load my-workflows.ccx
```

</TabItem>
<TabItem value="api" label="API">


```
GET    /api/v1/workflows/{id}/export    # Export workflow to JSON
POST   /api/v1/workflows/import         # Import workflow from JSON
```

See the [Workflows API Reference — Import/Export](../reference/api/workflows.md#export-and-import) for request/response details.

</TabItem>
</Tabs>


### Triggers

Triggers connect events to workflow execution. A trigger is not a fixed "type" — it binds a workflow to an `event_source` string (convention `{entity}.{action}`), matched by exact comparison when that event fires.

The workflow builder exposes the following selectable event sources:

| Event Source | Fires when |
|---|---|
| `manual` | Workflow is run manually by a user or API call |
| `node.create` | A new node is added to the knowledge graph (dispatched by import; powers the default Auto-Embed trigger) |
| `node.update` | An existing node is modified (powers the default Auto-Embed trigger) |
| `node.delete` | A node is removed from the graph |
| `edge.create` | A new relationship is added (dispatched by import) |
| `edge.update` / `edge.delete` | A relationship is modified / removed |
| `file.upload` | A new file is uploaded for processing |
| `file.indexed` | A file has been chunked and indexed for RAG |
| `import.complete` | Document entity extraction has finished |
| `custom` | A custom/webhook event source |

Today the platform actually dispatches `node.create`, `node.update`, and `edge.create` (the default Auto-Embed triggers ride on the node events); the remaining sources are selectable but not yet emitted. See the [Triggers API Reference — Event Sources](../reference/api/triggers.md#event-sources) for details.

<Tabs>
<TabItem value="web-ui" label="Web UI">


Triggers are managed on the **Triggers** tab of the **Automations** page. Toggle a trigger on or off there; for full configuration, use the **Edit in Workflow** link to open the trigger's workflow in the builder.

![Automations page showing the Triggers tab with event toggles](/img/screenshots/automations-triggers.png)

</TabItem>
<TabItem value="api" label="API">


```
GET    /api/v1/workflows/{id}/triggers  # List triggers for workflow
```

See the [Triggers API Reference](../reference/api/triggers.md) for full CRUD operations.

</TabItem>
</Tabs>


## Visual Workflow Builder

The workflow builder provides a visual drag-and-drop canvas for designing workflows. Access it from the **Workflows** tab on the **Automations** page in the web UI.

![Workflow builder with drag-and-drop step canvas](/img/screenshots/workflow-editor.png)

The builder supports:

- Drag-and-drop step placement from the tool palette
- Visual connections between steps
- Conditional branching with condition builder
- Step configuration via properties panel
- Test execution with live progress
- Execution history panel
