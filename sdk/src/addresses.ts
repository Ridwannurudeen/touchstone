export interface Deployment {
  readonly chainId: number;
  readonly legacyRegistryAddress: string;
  readonly v2RegistryAddress: string | null;
}

export const DEPLOYMENTS: Readonly<Record<string, Deployment>> = {
  xlayerTestnet: {
    chainId: 1952,
    legacyRegistryAddress: "0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C",
    v2RegistryAddress: null,
  },
  xlayerMainnet: {
    chainId: 196,
    legacyRegistryAddress: "0xc9d58e4496bF061C3177301Ff02518eBB70AD30d",
    v2RegistryAddress: null,
  },
};
