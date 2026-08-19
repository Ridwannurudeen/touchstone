import { concat, getBytes, hexlify, toUtf8Bytes } from "ethers";

export const ERC8021_SUFFIX = "0x80218021802180218021802180218021";

export function toBuilderCodeSuffix(codes: readonly string[]): string {
  if (codes.length === 0) {
    throw new Error("at least one Builder Code is required");
  }
  const joined = codes.join(",");
  const encoded = toUtf8Bytes(joined);
  if (encoded.length > 255) {
    throw new Error("Builder Code attribution must fit one length byte");
  }
  for (const code of codes) {
    if (!/^[\x20-\x7e]+$/.test(code) || code.includes(",")) {
      throw new Error("Builder Codes must be printable ASCII without commas");
    }
  }
  return hexlify(
    concat([
      encoded,
      Uint8Array.of(encoded.length),
      Uint8Array.of(0),
      getBytes(ERC8021_SUFFIX),
    ])
  );
}

export function appendBuilderCodeSuffix(data: string, codes: readonly string[]): string {
  return hexlify(concat([getBytes(data), getBytes(toBuilderCodeSuffix(codes))]));
}
