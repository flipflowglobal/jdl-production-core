import crypto from "crypto";

// Uses AES-256-GCM with key from ENCRYPTION_KEY env var
function getEncryptionKey(): Buffer {
  const key = process.env.ENCRYPTION_KEY || "\0".repeat(32);
  return Buffer.from(key.padEnd(32, "\0").slice(0, 32), "utf-8");
}

export function encryptPrivateKey(privateKey: string): { iv: string; authTag: string; encrypted: string } {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", getEncryptionKey(), iv);
  let encrypted = cipher.update(privateKey, "utf-8", "hex");
  encrypted += cipher.final("hex");
  const authTag = cipher.getAuthTag();
  return { iv: iv.toString("hex"), authTag: authTag.toString("hex"), encrypted };
}

export function decryptPrivateKey(encData: { iv: string; authTag: string; encrypted: string }): string {
  const decipher = crypto.createDecipheriv("aes-256-gcm", getEncryptionKey(), Buffer.from(encData.iv, "hex"));
  decipher.setAuthTag(Buffer.from(encData.authTag, "hex"));
  let decrypted = decipher.update(encData.encrypted, "hex", "utf-8");
  decrypted += decipher.final("utf-8");
  return decrypted;
}
