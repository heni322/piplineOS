import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LoginForm } from "@/components/LoginForm";

describe("LoginForm", () => {
  afterEach(() => vi.restoreAllMocks());

  it("has labelled, autocomplete-friendly fields", () => {
    render(<LoginForm />);
    expect(screen.getByLabelText("Work email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Authenticator code")).toHaveAccessibleDescription(/Required for operators/);
  });

  it("sends the CSRF-less login and reports success", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ role: "operator" }), { status: 200 }),
    );
    const onSuccess = vi.fn();
    render(<LoginForm onSuccess={onSuccess} />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "op@pipelineos.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret-password" } });
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ role: "operator" }));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/auth/login");
    expect(JSON.parse(init!.body as string)).toEqual({ email: "op@pipelineos.example", password: "secret-password", totp_code: "123456" });
  });

  it("shows a generic, accessible error without leaking which field was wrong", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ title: "Invalid credentials", status: 401 }), { status: 401 }),
    );
    render(<LoginForm onSuccess={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Those details didn’t work");
  });
});
