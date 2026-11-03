type ContractOcrRequest = {
  file: File;
  contractType: "jeonse" | "monthly_rent" | "sale";
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
