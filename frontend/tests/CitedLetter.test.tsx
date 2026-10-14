import { render, screen } from "@testing-library/react";
import { CitedLetter } from "@/components/CitedLetter";

describe("CitedLetter", () => {
  const claims = [{ text: "Contoso is hiring a Head of Service Delivery", source_url: "https://jobs.example/1" }];
  const numbering = new Map([["https://jobs.example/1", 1]]);

  it("highlights each sourced claim with a numbered link to its source", () => {
    render(<CitedLetter text={"Hi Alex,\n\nI saw that Contoso is hiring a Head of Service Delivery.\n\nJordan"} claims={claims} numbering={numbering} />);
    expect(screen.getByText("Contoso is hiring a Head of Service Delivery").tagName).toBe("MARK");
    const link = screen.getByRole("link", { name: /Source 1/ });
    expect(link).toHaveAttribute("href", "https://jobs.example/1");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders untrusted markup as text, never as HTML", () => {
    const { container } = render(<CitedLetter text={"<img src=x onerror=alert(1)> hello"} claims={[]} numbering={new Map()} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});

describe("CitedLetter links", () => {
  it("never renders a non-web source as a link", () => {
    const bad = [{ text: "new service desk role", source_url: "javascript:alert(1)" }];
    const { container } = render(
      <CitedLetter text="We saw your new service desk role." claims={bad} numbering={new Map([["javascript:alert(1)", 1]])} />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("[1]");
  });
});
