export function encryptPrivateKey(privateKey: string): { iv: string; authTag: string; encrypted: string } {
  return {
    iv: "placeholder_iv",
    authTag: "placeholder_tag",
    encrypted: Buffer.from(privateKey).toString("base64"),
  };
}

export function decryptPrivateKey(encData: { iv: string; authTag: string; encrypted: string }): string {
  return Buffer.from(encData.encrypted, "base64").toString("utf-8");
}
