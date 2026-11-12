# Native-reader research attribution

The experimental native page reader in `engine/native_records.py` uses page checksum and string-field format research from [TallyWeb](https://github.com/rudra-sah00/TallyWeb), commit `443c085f6b9f48520375b29da75eaead33310038`:

- `backend/internal/tallydb/reader.go`
- `backend/internal/tallydb/checksum.go`
- `docs/tally-binary-format.md`
- `docs/voucher-format.md`

The implementation was written in Python and checked against the supplied local company files. Upstream completeness and financial interpretation claims are not adopted. No upstream writer or binary executable is included or executed.

Upstream license:

```text
MIT License

Copyright (c) 2025 Rudra

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
