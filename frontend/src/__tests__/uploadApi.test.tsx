import { api, uploadAttachments } from "@/lib/api";

function file(name: string, body = "content") {
  return new File([body], name, { type: "text/plain" });
}

describe("uploadAttachments", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("builds multipart FormData with project_id and each file under files", async () => {
    const spy = jest.spyOn(api, "post").mockResolvedValue({
      data: { attachments: [{ id: "a1" }, { id: "a2" }] },
    } as any);

    const f1 = file("first.txt", "one");
    const f2 = file("second.txt", "two");
    const result = await uploadAttachments("p1", [f1, f2]);

    expect(result).toEqual([{ id: "a1" }, { id: "a2" }]);
    expect(spy).toHaveBeenCalledTimes(1);

    const [url, body, config] = spy.mock.calls[0];
    expect(url).toBe("/attachments");
    expect(body).toBeInstanceOf(FormData);
    const fd = body as FormData;
    expect(fd.get("project_id")).toBe("p1");
    expect(fd.has("conversation_id")).toBe(false);

    const files = fd.getAll("files") as File[];
    expect(files.map((f) => f.name)).toEqual(["first.txt", "second.txt"]);

    expect(config).toEqual({ headers: { "Content-Type": "multipart/form-data" } });
  });

  test("includes conversation_id when provided and defaults to an empty attachment list", async () => {
    const spy = jest.spyOn(api, "post").mockResolvedValue({ data: {} } as any);

    const result = await uploadAttachments("p1", [file("only.txt")], "c9");

    expect(result).toEqual([]);
    const fd = spy.mock.calls[0][1] as FormData;
    expect(fd.get("project_id")).toBe("p1");
    expect(fd.get("conversation_id")).toBe("c9");
    expect((fd.getAll("files") as File[]).map((f) => f.name)).toEqual(["only.txt"]);
  });
});
