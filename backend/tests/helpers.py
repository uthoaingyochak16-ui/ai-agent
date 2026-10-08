import io

from app.llm.providers import LLMResult


class FakeLLM:
    """Provider factory with scripted answers keyed by model name (str or callable(req)->str)."""
    def __init__(self, script=None):
        self.script = script or {}
        self.calls: list[tuple[str, object]] = []

    def __call__(self, name, key, base, timeout, retries):
        outer = self

        class P:
            async def generate(self, req, *, model, max_tokens, temperature):
                outer.calls.append((model, req))
                r = outer.script.get(model, "ok")
                text = r(req) if callable(r) else r
                if isinstance(text, Exception):
                    raise text
                return LLMResult(text=text, tokens_in=500, tokens_out=50, provider=name, model=model, latency_ms=3)
        return P()

    def models(self):
        return [m for m, _ in self.calls]


def make_pdf(pages: list[str]) -> bytes:
    """Minimal text PDF (one page per string)."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    x = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode())
    return out.getvalue()
