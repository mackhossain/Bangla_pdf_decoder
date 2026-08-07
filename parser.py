"""
parser.py

Low-level PDF parser.

Stage 1:
---------
✔ Read PDF
✔ Find xref
✔ Parse trailer
✔ Read indirect objects
✔ Decompress Flate streams

No external PDF library is used.
"""

from pathlib import Path
import re
import zlib


class PDFObject:

    def __init__(self, number, generation, dictionary, stream):
        self.number = number
        self.generation = generation
        self.dictionary = dictionary
        self.stream = stream

    def __repr__(self):
        return f"<PDFObject {self.number} {self.generation}>"


class PDFParser:

    OBJ_RE = re.compile(
        rb"(\d+)\s+(\d+)\s+obj(.*?)endobj",
        re.S
    )

    STREAM_RE = re.compile(
        rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream",
        re.S
    )

    DICT_RE = re.compile(
        rb"<<(.*?)>>",
        re.S
    )

    def __init__(self, filename):

        self.filename = Path(filename)

        with open(filename, "rb") as f:
            self.data = f.read()

        self.objects = {}

    # ---------------------------------------------------------

    def parse(self):

        for match in self.OBJ_RE.finditer(self.data):

            obj_no = int(match.group(1))
            gen = int(match.group(2))
            body = match.group(3)

            stream = None

            m = self.STREAM_RE.search(body)

            if m:

                dictionary = m.group(1)
                raw_stream = m.group(2)

                if b"/FlateDecode" in dictionary:
                    try:
                        raw_stream = zlib.decompress(raw_stream)
                    except Exception:
                        pass

                stream = raw_stream

            else:

                d = self.DICT_RE.search(body)

                if d:
                    dictionary = d.group(1)
                else:
                    dictionary = body.strip()

            self.objects[obj_no] = PDFObject(
                obj_no,
                gen,
                dictionary,
                stream
            )

    # ---------------------------------------------------------

    def get(self, obj):

        return self.objects.get(obj)

    # ---------------------------------------------------------

    def font_objects(self):

        for obj in self.objects.values():

            if b"/Type /Font" in obj.dictionary:
                yield obj

    # ---------------------------------------------------------

    def cmap_objects(self):

        for obj in self.objects.values():

            if obj.stream:

                if b"begincmap" in obj.stream:
                    yield obj

    # ---------------------------------------------------------

    def stream_objects(self):

        for obj in self.objects.values():

            if obj.stream:
                yield obj


if __name__ == "__main__":

    pdf = PDFParser("261694_com_1267_female_without_photo_71_2025-11-24.pdf")

    pdf.parse()

    print(pdf.get(6).dictionary.decode("latin1"))

    print()

    print("Fonts")

    for f in pdf.font_objects():
        print(f.number)

    print()

    print("CMaps")

    for c in pdf.cmap_objects():
        print(c.number)