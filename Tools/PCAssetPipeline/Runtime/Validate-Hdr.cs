using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace NZMOffline {
    public sealed class HdrValidation {
        public int Width { get; set; }
        public int Height { get; set; }
        public long DecodedPixels { get; set; }
        public bool CompleteRlePayload { get; set; }
    }
    public static class HdrValidator {
        private static string Line(BinaryReader reader) {
            var text = new StringBuilder();
            while (true) {
                byte value = reader.ReadByte();
                if (value == 10) return text.ToString().TrimEnd('\r');
                if (text.Length >= 4096) throw new InvalidDataException("HDR header line too long.");
                text.Append((char)value);
            }
        }
        public static HdrValidation Validate(string path) {
            using var stream = File.OpenRead(path);
            using var reader = new BinaryReader(stream, Encoding.ASCII, true);
            string signature = Line(reader);
            if (signature != "#?RADIANCE" && signature != "#?RGBE") throw new InvalidDataException("HDR signature missing.");
            bool format = false;
            string line;
            for (int count = 0; ; count++) {
                if (count > 128) throw new InvalidDataException("HDR header is too long.");
                line = Line(reader);
                if (line.Length == 0) break;
                if (line == "FORMAT=32-bit_rle_rgbe") format = true;
            }
            if (!format) throw new InvalidDataException("Unsupported HDR pixel format.");
            var size = Regex.Match(Line(reader), @"^-Y (\d+) \+X (\d+)$");
            if (!size.Success) throw new InvalidDataException("Unsupported HDR orientation or dimensions.");
            int width = int.Parse(size.Groups[2].Value), height = int.Parse(size.Groups[1].Value);
            if (width < 8 || width > 32767 || height < 1 || height > 32767) throw new InvalidDataException("HDR dimensions out of range.");
            for (int y = 0; y < height; y++) {
                if (reader.ReadByte() != 2 || reader.ReadByte() != 2 || ((reader.ReadByte() << 8) | reader.ReadByte()) != width)
                    throw new InvalidDataException("HDR scanline width or RLE marker mismatch.");
                for (int channel = 0; channel < 4; channel++) {
                    int decoded = 0;
                    while (decoded < width) {
                        int code = reader.ReadByte();
                        int length = code > 128 ? code - 128 : code;
                        if (length == 0 || decoded + length > width) throw new InvalidDataException("HDR RLE run out of bounds.");
                        int stored = code > 128 ? 1 : length;
                        if (reader.ReadBytes(stored).Length != stored) throw new EndOfStreamException("Truncated HDR pixel payload.");
                        decoded += length;
                    }
                }
            }
            if (stream.Position != stream.Length) throw new InvalidDataException("Unexpected trailing HDR bytes.");
            return new HdrValidation { Width=width, Height=height, DecodedPixels=(long)width*height, CompleteRlePayload=true };
        }
    }
}
