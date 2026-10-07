# Architecture (arc42)

This is the architecture documentation of `open-california`, organised after the
[arc42](https://arc42.org) template and drawn with the [C4 model](https://c4model.com)
(context, container, component, deployment). It is the *structured* view; the five-minute
walkthrough is [Architecture overview](../architecture.md), and the dated evidence behind every
protocol claim is in the [reverse-engineering notes](../business-logic/index.md).

Diagrams are Mermaid flowcharts styled as C4 (person, system, container, component), so the site
builds without Java or a diagram server. Solid arrows are runtime data flow, dashed arrows are
build-time or CI relationships.

| arc42 section | Page |
|---|---|
| 1 Introduction and goals, 2 Constraints, 3 Context and scope | [Goals, constraints and context](context-and-scope.md) |
| 4 Solution strategy, 5 Building block view | [Strategy and building blocks](building-blocks.md) |
| 6 Runtime view, 7 Deployment view | [Runtime and deployment](runtime-and-deployment.md) |
| 8 Crosscutting concepts, 9 Decisions | [Crosscutting concepts and decisions](crosscutting-and-decisions.md) |
| 10 Quality requirements, 11 Risks and technical debt, 12 Glossary | [Quality, risks and glossary](quality-risks-glossary.md) |

```{toctree}
:hidden:
:maxdepth: 1

context-and-scope
building-blocks
runtime-and-deployment
crosscutting-and-decisions
quality-risks-glossary
```
