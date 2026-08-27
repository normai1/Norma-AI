import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoleBadge } from "./ui";

describe("RoleBadge", () => {
  it("renders the role text", () => {
    render(<RoleBadge role="admin" />);

    expect(screen.getByText("admin")).toBeInTheDocument();
  });
});
