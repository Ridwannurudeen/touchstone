export interface Deployment {
  readonly chainId: number;
  readonly legacyRegistryAddress: string;
  readonly v2RegistryAddress: string | null;
  /** Block in which the v2 registry was deployed: the earliest block worth indexing. */
  readonly v2RegistryDeploymentBlock: number | null;
}

export const DEPLOYMENTS: Readonly<Record<string, Deployment>> = {
  xlayerTestnet: {
    chainId: 1952,
    legacyRegistryAddress: "0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C",
    v2RegistryAddress: "0xBaE680e671e0451b95c9b09eD15F70C3E1EA7720",
    v2RegistryDeploymentBlock: 38699818,
  },
  xlayerMainnet: {
    chainId: 196,
    legacyRegistryAddress: "0xc9d58e4496bF061C3177301Ff02518eBB70AD30d",
    v2RegistryAddress: "0x0dAb4A5B7dd24434Ab6564734E26d3d76985352C",
    v2RegistryDeploymentBlock: 68389940,
  },
};
