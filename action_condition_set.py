from typing import List, Tuple, Dict, Any, TYPE_CHECKING

from sequence.entanglement_management.purification import BBPSSWProtocol
from sequence.resource_management.memory_manager import MemoryInfo

from sequence.entanglement_management.generation.generation_base import EntanglementGenerationA
from sequence.entanglement_management.swapping import EntanglementSwappingA, EntanglementSwappingB
from sequence.resource_management.action_condition_set import Arguments, RequestFunction, ActionReturn, TempMemory, TempNode

from swapping import EntanglementSwappingA_bds, EntanglementSwappingB_bds

if TYPE_CHECKING:
    from sequence.components.memory import Memory
    from sequence.entanglement_management.entanglement_protocol import EntanglementProtocol


# 1. entanglement generation #

def eg_rule_action_await_adaptive(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used by entanglement generation protocol on nodes except the initiator, i.e., index > 0
    """
    memories: list[Memory] = [info.memory for info in memories_info]
    memory = memories[0]
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    from_app_request = args["from_app_request"]
    protocol = EntanglementGenerationA.create(owner=TempNode, name=f"EGA.{memory.name}",
                                              middle=mid, other=path[index - 1], memory=memory,
                                              from_app_request=from_app_request)

    return protocol, [None], [None], [None]


def eg_rule_action_request_adaptive(memories_info: list[MemoryInfo], args: Arguments) -> ActionReturn:
    """Action function used by entanglement generation protocol on nodes except the responder, i.e., index < len(path) - 1
    """
    mid = args["mid"]
    path = args["path"]
    index = args["index"]
    memories = [info.memory for info in memories_info]
    memory = memories[0]
    from_app_request = args["from_app_request"]
    protocol = EntanglementGenerationA.create(owner=TempNode, name=f"EGA.{memory.name}",
                                              middle=mid, other=path[index + 1], memory=memory,
                                              from_app_request=from_app_request)
    req_args = {"name": args["name"], "reservation": args["reservation"]}
    return protocol, [path[index + 1]], [eg_match_func_adaptive], [req_args]


def eg_match_func_adaptive(protocols: list[EntanglementProtocol], args: Arguments) -> EntanglementGenerationA | None:
    """Function used by `eg_rule_action2` function for selecting generation protocols on the remote node
    Args:
        protocols: the waiting protocols (wait for request)
        args: arguments from the node who sent the request
    Return:
        the selected protocol (there could be multiple protocols that satisfy the condition, if so, return the first protocol)
    """
    name = args["name"]
    reservation = args["reservation"]
    for protocol in protocols:
        if (isinstance(protocol, EntanglementGenerationA)
                and protocol.remote_node_name == name
                and protocol.rule.get_reservation() == reservation):
            return protocol
    return None


# 3. entanglement swapping #

def es_rule_action_A_adaptive(memories_info: List["MemoryInfo"], args: Arguments) -> Tuple[
    EntanglementSwappingA | EntanglementSwappingA_bds, List[str], List["es_req_func_adaptive"], List[Dict]]:
    """Action function used by EntanglementSwappingA protocol on nodes
    """
    es_succ_prob = args["es_succ_prob"]
    memories = [info.memory for info in memories_info]
    encoding_type = args["encoding_type"]

    if encoding_type == "single_atom":
        es_degradation = args["es_degradation"]
        name = "ESA.{}.{}".format(memories[0].name, memories[1].name)
        protocol = EntanglementSwappingA(None, name, memories[0], memories[1], es_succ_prob, es_degradation)
    elif encoding_type == 'single_heralded':
        is_twirled = args['is_twirled']
        name = "ESA_bds.{}.{}".format(memories[0].name, memories[1].name)
        protocol = EntanglementSwappingA_bds(None, name, memories[0], memories[1], es_succ_prob, is_twirled)

    dsts = [info.remote_node for info in memories_info]
    req_funcs = [es_req_func_adaptive, es_req_func_adaptive]
    req_args = [{"target_memo": memories_info[0].remote_memo}, {"target_memo": memories_info[1].remote_memo}]
    return protocol, dsts, req_funcs, req_args


def es_rule_action_B_adaptive(memories_info: List["MemoryInfo"], args: Arguments) -> Tuple[
    EntanglementSwappingB | EntanglementSwappingB_bds, List[None], List[None], List[None]]:
    """Action function used by EntanglementSwappingB protocol
    """
    memories = [info.memory for info in memories_info]
    memory = memories[0]
    encoding_type = args["encoding_type"]
    if encoding_type == "single_atom":
        protocol = EntanglementSwappingB(None, "ESB." + memory.name, memory)
    elif encoding_type == 'single_heralded':
        protocol = EntanglementSwappingB_bds(None, "ESB_bds." + memory.name, memory)
    return protocol, [None], [None], [None]


def es_req_func_adaptive(protocols: List["EntanglementProtocol"],
                         args: Arguments) -> EntanglementSwappingB | EntanglementSwappingB_bds | None:
    """Function used by `es_rule_actionA` for selecting swapping protocols on the remote node
    """
    target_memo = args["target_memo"]
    for protocol in protocols:
        if (isinstance(protocol, EntanglementSwappingB | EntanglementSwappingB_bds)
                # and protocol.memory.name == memories_info[0].remote_memo):
                and protocol.memory.name == target_memo):
            return protocol
    return None
