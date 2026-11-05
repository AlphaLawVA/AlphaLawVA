export type ContractOcrRequest = {
  contract: {
    file: File;
    contractType: "jeonse" | "monthly_rent" | "sale";
  };
  // 계약서와 함께 올린 등기사항증명서. 없으면 계약서만 인식한다.
  registryFile?: File;
};

let pendingRequest: ContractOcrRequest | null = null;

export function setPendingContractOcrRequest(request: ContractOcrRequest) {
  pendingRequest = request;
}

export function takePendingContractOcrRequest(): ContractOcrRequest | null {
  const request = pendingRequest;
  pendingRequest = null;
  return request;
}

export function clearPendingContractOcrRequest() {
  pendingRequest = null;
}
