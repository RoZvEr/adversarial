import torch
from torch import autograd
from model_utils import ARCHS_LIST, get_model, load_model, predict
from file_utils import validate_save_file_location
import argparse
from pgd import get_current_time
import os


def get_gradient(model, x, label, criterion, similarity_coeffs=None):
    x = autograd.Variable(x, requires_grad=True).cuda()

    if type(model) is list:
        if similarity_coeffs is None:
            similarity_coeffs = dict(zip([i for i in range(len(model))], [1/len(model)]*len(model)))
        loss = torch.zeros(1)
        for index, current_model in enumerate(model):
            prediction = predict(current_model, x)
            current_loss = criterion(prediction, label)
            loss = torch.add(loss, similarity_coeffs[list(similarity_coeffs.keys())[index]] * current_loss)
    else:
        prediction = predict(model, x)
        loss = criterion(prediction, label)

    grad = autograd.grad(loss, x)[0]
    return grad.cpu()


def get_sorted_order(grad, size):
    grad = torch.flatten(grad)
    if not 0 < size < grad.size(0):
        raise ValueError('Invalid size entered!')

    order = torch.argsort(grad.cpu(), descending=True)[:size]
    return order


def get_grad_dict(model, criterion, args_dict):
    grads_dict = {}

    for category_file in os.listdir(args_dict['dataset']):
        category_grads = []
        if category_file.endswith('.pt'):
            dataset = torch.load(os.path.join(args_dict['dataset'], category_file))

            if dataset.__len__() == 0:
                continue
            for image, _ in dataset:
                prediction = predict(model, image.cuda())
                label = torch.argmax(prediction, dim=1).cuda()

                current_grad = get_gradient(model, image, label, criterion)
                category_grads.append(current_grad.cpu())

            grads_dict[dataset.category] = category_grads

    return grads_dict


def normalize_grad(grad):
    mean_grad = torch.cuda.FloatTensor([[[torch.mean(grad[0])]],
                                        [[torch.mean(grad[1])]],
                                        [[torch.mean(grad[2])]]]).repeat(1, grad.size(1), grad.size(2))
    std_grad = torch.cuda.FloatTensor([[[torch.std(grad[0])]],
                                       [[torch.std(grad[1])]],
                                       [[torch.std(grad[2])]]]).repeat(1, grad.size(1), grad.size(2))

    normalized_grad = (grad.cuda() - mean_grad) / std_grad
    return normalized_grad.cpu()


def normalize_grads_dict(grads_dict):
    for key in grads_dict.keys():
        grads = grads_dict[key]
        for i in range(0, len(grads)):
            grads[i] = normalize_grad(grads[i])
        grads_dict[key] = grads
    return grads_dict


def get_averages(grad, mask):
    grad_abs = grad*torch.sign(grad)

    num_values = mask.size(0) * mask.size(1) * mask.size(2)
    num_ones = torch.sum(mask)
    num_zeros = num_values - num_ones

    foreground_grad_sum = torch.sum(grad_abs * mask)
    background_grad_sum = torch.sum(grad_abs) - foreground_grad_sum

    foreground_grad_average = foreground_grad_sum / num_ones
    background_grad_average = background_grad_sum / num_zeros
    return foreground_grad_average, background_grad_average


def get_category_average(grads, dataset):
    foreground_average = 0
    background_average = 0

    for grad, (_, mask) in zip(grads, dataset):
        foreground_grad_average, background_grad_average = get_averages(grad, mask)
        foreground_average += foreground_grad_average
        background_average += background_grad_average

    foreground_average /= dataset.__len__()
    background_average /= dataset.__len__()

    return foreground_average.cpu(), background_average.cpu()


def get_averages_by_category(grads_dict, args_dict):
    categories_averages = {}
    for category in grads_dict.keys():
        category_dataset = torch.load(os.path.join(args_dict['dataset'], category+'.pt'))
        foreground_average, background_average = get_category_average(grads_dict[category], category_dataset)
        categories_averages[category] = [foreground_average, background_average]
    return categories_averages


def get_averages_dict(model, criterion, args_dict):
    averages_dict = {}

    for category_file in os.listdir(args_dict['dataset']):
        category_grads = []
        if category_file.endswith('.pt'):
            dataset = torch.load(os.path.join(args_dict['dataset'], category_file))

            if dataset.__len__() == 0:
                continue
            for image, _ in dataset:
                prediction = predict(model, image.cuda())
                label = torch.argmax(prediction, dim=1).cuda()

                current_grad = get_gradient(model, image, label, criterion)
                if args_dict['normalize_grads']:
                    current_grad = normalize_grad(current_grad)
                category_grads.append(current_grad.cpu())

            foreground_average, background_average = get_category_average(category_grads, dataset)
            averages_dict[dataset.category] = [foreground_average, background_average]

    return averages_dict


def main():
    time = get_current_time()

    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', type=str, choices=ARCHS_LIST, default='resnet50')
    parser.add_argument('--pretrained', default=False, action='store_true')
    parser.add_argument('--checkpoint_location', type=str, default=None)
    parser.add_argument('--from_robustness', default=False, action='store_true')
    parser.add_argument('--dataset', type=str, default='dataset/coco')
    parser.add_argument('--normalize_grads', default=False, action='store_true')
    parser.add_argument('--save_file_location', type=str, default='results/gradient/' + time + '.pt')
    args_dict = vars(parser.parse_args())

    validate_save_file_location(args_dict['save_file_location'])

    if args_dict['checkpoint_location'] is not None:
        model = load_model(location=args_dict['checkpoint_location'],
                           arch=args_dict['arch'],
                           from_robustness=args_dict['from_robustness']).cuda().eval()
    else:
        model = get_model(args_dict['arch'], 'standard' if [args_dict['pretrained']] else None).cuda().eval()

    criterion = torch.nn.CrossEntropyLoss(reduction='none')

    averages = get_averages_dict(model, criterion, args_dict)
    torch.save({'averages': averages, 'args': args_dict},
               args_dict['save_file_location'])


if __name__ == '__main__':
    main()
