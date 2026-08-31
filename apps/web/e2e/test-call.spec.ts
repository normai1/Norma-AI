import { expect, test } from "@playwright/test";

const API_URL = "http://localhost:8000";

// Real microphone permission and audio device, faked so the browser never
// shows a real permission prompt and getUserMedia resolves with a synthetic
// stream - this is the mic-permission-to-connected path a real human would
// otherwise click through by hand. Applies to every test in this file (a
// top-level test.use() is required - Playwright forces a new worker for
// launchOptions and refuses it inside a describe block).
test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  },
});

test("test-call page redirects an unauthenticated visitor to login", async ({ page }) => {
  await page.goto("/assistants/00000000-0000-0000-0000-000000000000/test-call");

  await expect(page).toHaveURL(/\/login$/, { timeout: 15000 });
});

test.describe("authenticated golden path", () => {
  test("start test call reaches Connected", async ({ page, request, context }) => {
    const unique = Date.now();
    const email = `e2e-test-call-${unique}@example.com`;
    const password = "a-strong-password";

    const registerResponse = await request.post(`${API_URL}/api/v1/auth/register`, {
      data: { email, password },
    });
    expect(registerResponse.ok()).toBeTruthy();
    const auth = await registerResponse.json();

    const authHeaders = { Authorization: `Bearer ${auth.access_token}` };

    const orgResponse = await request.post(`${API_URL}/api/v1/organizations`, {
      headers: authHeaders,
      data: { name: `E2E Org ${unique}` },
    });
    expect(orgResponse.ok()).toBeTruthy();
    const organizationId = (await orgResponse.json()).id;

    const workspaceResponse = await request.post(
      `${API_URL}/api/v1/organizations/${organizationId}/workspaces`,
      { headers: authHeaders, data: { name: `E2E Workspace ${unique}` } },
    );
    expect(workspaceResponse.ok()).toBeTruthy();
    const workspaceId = (await workspaceResponse.json()).id;

    const assistantResponse = await request.post(
      `${API_URL}/api/v1/organizations/${organizationId}/workspaces/${workspaceId}/assistants`,
      { headers: authHeaders, data: { name: `E2E Assistant ${unique}` } },
    );
    expect(assistantResponse.ok()).toBeTruthy();
    const assistantId = (await assistantResponse.json()).id;

    await context.grantPermissions(["microphone"]);

    await page.addInitScript(
      ({ accessToken, refreshToken }) => {
        window.localStorage.setItem("norma.access_token", accessToken);
        window.localStorage.setItem("norma.refresh_token", refreshToken);
      },
      { accessToken: auth.access_token, refreshToken: auth.refresh_token },
    );

    await page.goto(`/assistants/${assistantId}/test-call`);

    await expect(page.getByRole("button", { name: "Start test call" })).toBeVisible({
      timeout: 15000,
    });
    await page.getByRole("button", { name: "Start test call" }).click();

    await expect(page.getByText("Connected", { exact: true })).toBeVisible({
      timeout: 15000,
    });
  });
});
