# Visual colony playback

The colony viewer appears below project status when the accepted program has test inputs with `state.tick`, `state.organisms`, `state.food` and `action.type = "step"`. Organisms require unique `id` values, finite `x`, `y` and `energy`. Food uses finite `x`, `y`. Projects with other contracts retain the manual JSON playground.

## Using it

Refresh the page after updating `static/chat.js`. Choose the demo colony or one of the saved test inputs. The demo starts with 8 organisms and 40 food locations; these are explicitly authored initial conditions, not a claimed product of autonomous evolution.

Use **1 тик**, **10 тиков**, or **Автопоказ**. Each next frame comes from the existing authenticated `POST /api/missions/{id}/try` endpoint. The accepted Python program runs in the existing server sandbox; the browser only draws its returned state. It neither evaluates generated Python/JavaScript nor uses the language model to invent frames.

The scene includes organisms, food, energy rings, population/energy/food counters, a population chart, state-difference events and an organism table. Clicking an organism shows its coordinates, energy and genes. JSON remains available in collapsed debugging sections. The coordinate grid auto-fits; it does not introduce world boundaries or wraparound rules that the program does not implement.

## Two independent processes

**Development** keeps searching for accepted code revisions on the server. Viewer controls never call project pause, resume, guidance or creation endpoints.

**Playback** is a separate series of executions using browser-held scene state. Stopping or resetting it does not stop development. Hiding/closing the tab stops playback, not development. Reloading the page resets the local scene. Playback is limited to 200 ticks per start and stops on extinction, invalid output, failed execution or a non-increasing tick.

The endpoint executes the latest accepted program rather than supporting version pinning. The viewer therefore checks the returned version on every step. If a newer revision appears, playback stops and preserves its last frame. Applying the new revision explicitly resets the scene; results from different versions are not silently joined.

## Deployment and limits

The viewer is bundled into the already-served `static/chat.js`, so this change needs no new route and no application/model restart. Back up the previous file and replace it atomically. Existing open tabs require a refresh. No backend, evaluator, regression tests or mission data need alteration.

Rendering accepts at most 1000 organisms, 3000 food objects and coordinates within ±1,000,000. Input requests are limited to 15 KB, below the server endpoint limit. The table shows the first 100 organisms. Energy-ring saturation is 30 units; the table/counters retain actual energy values. The chart retains the latest 200 displayed states. State-difference events report IDs appearing/disappearing and decreases in food count, not unverified biological causes.

The demo is not an independent correctness test of the evolved rules. Passing generated regression tests does not prove correctness, and the visualization does not add reproduction, navigation or other mechanics absent from the accepted program.

## Tests

With Node.js, Playwright and its Chromium runtime available:

```sh
node --check static/chat.js
node colony-viewer.test.cjs
```

`PLAYWRIGHT_MODULE` can point to an existing Playwright module and `RCP_UI_DIR` can point to a staged directory containing `autopilot.html` and `chat.js`. Browser tests use synthetic local responses, not a live model. They cover real response rendering, errors, version changes, stopping in-flight work, sequential ticks, validation, responsive layouts, preserved input and the absence of development-control requests.
